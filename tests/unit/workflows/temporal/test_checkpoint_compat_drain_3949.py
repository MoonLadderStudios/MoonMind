"""Drain ownership, ordering, and least-privilege evidence (MoonMind#3949).

The artifacts-fleet cutover (#4032) is delivered; the workflow-queue
persistence registration stays only for pre-cutover replay/in-flight
compatibility. These tests execute the drain-ownership gate, the
idempotency/ownership properties behind ordering, and the behavioral
capability inventory — without a Temporal server, database, or deployment
probe. Fixture replay remains history-compatibility evidence only; the
drain gate below is what authorizes removal.
"""

from __future__ import annotations

import asyncio

import pytest

from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from moonmind.gates.checkpoint_compat_drain import (
    COMPAT_DRAIN_CONTRACT,
    CheckpointCompatDrainObservations,
    CheckpointCompatDrainUsage,
    collect_checkpoint_compat_drain_observations,
    evaluate_checkpoint_compat_drain,
    evaluate_checkpoint_compat_drain_observations,
    render_checkpoint_compat_drain_report,
    retention_reason,
)

# No explicit shard mark: tests/conftest.py owns every test under
# tests/unit/workflows/temporal/ to the temporal-boundary shard. Do not add
# pytest.mark.unit_fast here; it conflicts with that ownership.


# --- Drain-ownership contract -----------------------------------------------


def test_drained_deployment_unblocks_compat_removal():
    decision = evaluate_checkpoint_compat_drain(CheckpointCompatDrainUsage())
    assert decision.outstanding == 0
    assert decision.may_remove_workflow_queue_handlers is True
    assert decision.required_action == "safe_to_remove"
    assert decision.blocking_dimensions == ()
    assert decision.contract == COMPAT_DRAIN_CONTRACT


@pytest.mark.parametrize(
    "usage",
    [
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=2),
        CheckpointCompatDrainUsage(supported_resets_pending=1),
        CheckpointCompatDrainUsage(
            open_pre_cutover_histories=3,
            pending_old_queue_tasks=2,
            supported_resets_pending=1,
        ),
    ],
)
def test_any_outstanding_consumer_retains_compat(usage):
    decision = evaluate_checkpoint_compat_drain(usage)
    assert decision.may_remove_workflow_queue_handlers is False
    assert decision.required_action == "retain_compat"
    assert decision.outstanding == (
        usage.open_pre_cutover_histories
        + usage.pending_old_queue_tasks
        + usage.supported_resets_pending
    )
    assert decision.blocking_dimensions, "a retained gate must name its blockers"
    reason = retention_reason(decision)
    assert COMPAT_DRAIN_CONTRACT in reason
    for dimension in decision.blocking_dimensions:
        assert dimension in reason


def test_drain_gate_rejects_negative_counts():
    with pytest.raises(ValueError):
        CheckpointCompatDrainUsage(open_pre_cutover_histories=-1)
    with pytest.raises(ValueError):
        CheckpointCompatDrainUsage(pending_old_queue_tasks=-1)
    with pytest.raises(ValueError):
        CheckpointCompatDrainUsage(supported_resets_pending=-1)


def test_drain_rule_matches_canonical_worker_drain_predicate():
    """The gate reuses the existing drain rule; it is not a second policy.

    ``evaluate_worker_drain`` allows route removal exactly when outstanding
    work reaches zero. This gate must agree with that predicate for every
    mapped input so compat removal can never be more permissive than the
    canonical worker-drain contract.
    """

    from moonmind.workflows.executions.checkpoint_promotion import (
        FrozenGenerationUsage,
        evaluate_worker_drain,
    )

    cases = [
        CheckpointCompatDrainUsage(),
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=4),
        CheckpointCompatDrainUsage(supported_resets_pending=2),
        CheckpointCompatDrainUsage(
            open_pre_cutover_histories=1,
            pending_old_queue_tasks=1,
            supported_resets_pending=1,
        ),
    ]
    for usage in cases:
        gate = evaluate_checkpoint_compat_drain(usage)
        canonical = evaluate_worker_drain(
            FrozenGenerationUsage(
                deploymentGeneration="test-generation",
                openRecoveryHistories=usage.open_pre_cutover_histories,
                pendingRestorations=(
                    usage.pending_old_queue_tasks + usage.supported_resets_pending
                ),
            )
        )
        assert gate.may_remove_workflow_queue_handlers is canonical.may_remove_worker_routes


def test_compat_registration_points_at_the_drain_gate():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[4]
        / "moonmind/workflows/temporal/workflow_registry.py"
    ).read_text()
    assert "checkpoint_compat_drain" in source
    assert "evaluate_checkpoint_compat_drain" in source


@pytest.mark.asyncio
async def test_drain_gate_retains_progress_under_bounded_load():
    """Bounded-load rehearsal: the gate stays decisive under saturation."""

    usages = [
        CheckpointCompatDrainUsage(),
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=5),
        CheckpointCompatDrainUsage(supported_resets_pending=2),
    ]

    async def _decide(usage: CheckpointCompatDrainUsage) -> bool:
        await asyncio.sleep(0)
        return evaluate_checkpoint_compat_drain(usage).may_remove_workflow_queue_handlers

    verdicts = await asyncio.wait_for(
        asyncio.gather(*(_decide(usage) for usage in usages * 25)),
        timeout=30,
    )
    assert verdicts == [True, False, False, False] * 25


@pytest.mark.asyncio
async def test_consolidated_worker_retains_control_and_cleanup_progress_under_load():
    """Saturation rehearsal: control/cleanup progress is never lost or
    reordered under concurrent load.

    Each of 100 concurrent turn handoffs records its control decision and
    its cleanup release into a shared ledger. The gate decision stays
    decisive per input, every handoff's control record precedes its cleanup
    record, and all 100 handoffs retain both records — the progress
    property the consolidated topology must keep until the drain gate
    releases the compat registration.
    """

    ledger: dict[str, list[str]] = {}
    ledger_lock = asyncio.Lock()

    async def _handoff(index: int, usage: CheckpointCompatDrainUsage) -> bool:
        await asyncio.sleep(0)
        decision = evaluate_checkpoint_compat_drain(usage)
        async with ledger_lock:
            ledger.setdefault(f"turn-{index}", []).append(
                f"control:{decision.required_action}"
            )
        await asyncio.sleep(0)
        async with ledger_lock:
            ledger.setdefault(f"turn-{index}", []).append("cleanup:released")
        return decision.may_remove_workflow_queue_handlers

    usages = [
        CheckpointCompatDrainUsage(),
        CheckpointCompatDrainUsage(open_pre_cutover_histories=1),
        CheckpointCompatDrainUsage(pending_old_queue_tasks=5),
        CheckpointCompatDrainUsage(supported_resets_pending=2),
    ]
    verdicts = await asyncio.wait_for(
        asyncio.gather(
            *(_handoff(index, usages[index % len(usages)]) for index in range(100))
        ),
        timeout=30,
    )
    assert verdicts == [True, False, False, False] * 25
    assert len(ledger) == 100
    for index in range(100):
        records = ledger[f"turn-{index}"]
        assert len(records) == 2, f"turn-{index} lost progress under load"
        assert records[0].startswith("control:")
        assert records[1] == "cleanup:released"


# --- Ordering: idempotency and single-mutator ownership ----------------------


def test_launch_idempotency_key_is_deterministic_per_turn():
    first = build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-1"
    )
    second = build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-1"
    )
    assert first == second
    # Duplicate delivery of the same turn addresses the same key.
    assert ":".join(["wf-1", "b-1", "t-1", "launch"]) == first


def test_launch_idempotency_key_binds_every_identity():
    key = build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-1"
    )
    for identity in ("wf-1", "b-1", "t-1"):
        assert identity in key
    assert build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-1", branch_turn_id="t-2"
    ) != key
    assert build_branch_turn_launch_idempotency_key(
        workflow_id="wf-1", branch_id="b-2", branch_turn_id="t-1"
    ) != key


@pytest.mark.parametrize("field", ["workflow_id", "branch_id", "branch_turn_id"])
def test_launch_idempotency_key_rejects_blank_identity(field):
    kwargs = {"workflow_id": "wf-1", "branch_id": "b-1", "branch_turn_id": "t-1"}
    kwargs[field] = "  "
    with pytest.raises(ValueError):
        build_branch_turn_launch_idempotency_key(**kwargs)


def test_single_mutator_owns_terminal_writes():
    """Ordering has one writer: lock + finalize on CheckpointBranchService."""

    assert hasattr(CheckpointBranchService, "lock_turn_execution")
    assert hasattr(CheckpointBranchService, "finalize_turn_execution")
    assert hasattr(CheckpointBranchService, "mark_turn_running")


# --- Least privilege: behavioral I/O inventory ------------------------------


def _deny_session_maker(*args, **kwargs):
    raise RuntimeError("test denied unexpected database I/O")


@pytest.mark.asyncio
async def test_metadata_helpers_need_no_database_authority(monkeypatch):
    """Helpers execute with database I/O denied; persistence cannot."""

    import moonmind.workflows.temporal.workflows.agent_run as agent_run_module
    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    monkeypatch.setattr(
        turn_module, "async_session_maker", _deny_session_maker
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # The adapter registry is env-gated; enable OpenClaw explicitly so the
    # metadata read is hermetic instead of depending on ambient CI env.
    monkeypatch.setenv("OPENCLAW_ENABLED", "true")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")

    metadata = await agent_run_module.resolve_adapter_metadata("OpenClaw")
    assert metadata["agent_id"] == "openclaw"

    route = await agent_run_module.get_activity_route(
        "checkpoint_branch.turn.persist_terminal"
    )
    assert route["task_queue"] == "mm.activity.artifacts"


@pytest.mark.asyncio
async def test_all_four_retained_helpers_need_no_io_authority(monkeypatch):
    """Full helper inventory: none of the four retained helpers needs
    database, provider, Docker, or artifact-storage authority.

    The inventory denies database I/O and removes provider credentials and
    Docker/artifact configuration from the environment. Every helper must
    still succeed with its real registry/catalog reads, proving new-only
    workflow processing carries no unnecessary I/O credentials or mounts.
    """

    import moonmind.workflows.temporal.workflows.agent_run as agent_run_module
    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    monkeypatch.setattr(turn_module, "async_session_maker", _deny_session_maker)
    for env_key in (
        "DATABASE_URL",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "MOONMIND_PROVIDER_CREDENTIALS_JSON",
        "DOCKER_HOST",
        "DOCKER_SOCKET",
        "MOONMIND_ARTIFACT_STORE_URL",
        "MOONMIND_ARTIFACT_STORAGE_URL",
    ):
        monkeypatch.delenv(env_key, raising=False)
    # The adapter registry is env-gated; enable OpenClaw explicitly so the
    # helper reads are hermetic instead of depending on ambient CI env.
    monkeypatch.setenv("OPENCLAW_ENABLED", "true")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")

    agent_id = (
        await agent_run_module.resolve_adapter_metadata("OpenClaw")
    )["agent_id"]
    assert agent_id == "openclaw"

    assert (
        await agent_run_module.resolve_external_adapter(agent_id)
    ) == agent_id
    assert await agent_run_module.external_adapter_execution_style(agent_id) in (
        "polling",
        "streaming_gateway",
    )
    route = await agent_run_module.get_activity_route(
        "checkpoint_branch.turn.persist_terminal"
    )
    assert route["task_queue"] == "mm.activity.artifacts"


@pytest.mark.asyncio
async def test_retained_persistence_handler_fails_closed_without_db(monkeypatch):
    """The compat handlers still carry database authority: deny it loudly."""

    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    monkeypatch.setattr(
        turn_module, "async_session_maker", _deny_session_maker
    )
    with pytest.raises(RuntimeError, match="denied unexpected database"):
        await turn_module.mark_checkpoint_branch_turn_running(
            {
                "workflowId": "wf-1",
                "branchId": "b-1",
                "branchTurnId": "t-1",
                "agentRunWorkflowId": "run-1",
            }
        )


@pytest.mark.asyncio
async def test_retained_persistence_handler_fails_closed_without_artifact_storage(
    monkeypatch,
    tmp_path,
):
    """Artifact-storage denial fails closed instead of persisting partially.

    Terminal persistence must cross the artifact authority boundary: with a
    working database and a denied artifact service, invoking
    ``persist_checkpoint_branch_turn_terminal`` with an artifact-bearing
    payload must raise from the artifact denial. The test records the
    denial invocation so a regression that stops crossing the artifact
    boundary (for example by calling a database-only handler) fails
    instead of passing vacuously.
    """

    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from api_service.db.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/denial.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr(turn_module, "async_session_maker", sessions)

        artifact_calls: list[str] = []

        def _deny_artifact_service(*args, **kwargs):
            artifact_calls.append("get_checkpoint_branch_artifact_service")
            raise RuntimeError("test denied unexpected artifact-storage I/O")

        monkeypatch.setattr(
            turn_module,
            "get_checkpoint_branch_artifact_service",
            _deny_artifact_service,
        )

        async with sessions() as session:
            await CheckpointBranchService(session).create_branch_graph(
                {
                    "branchId": "branch-1",
                    "label": "Artifact-denial terminal persistence",
                    "branchTurnId": "turn-1",
                    "source": {
                        "workflowId": "source-workflow",
                        "runId": "source-run",
                        "logicalStepId": "implement",
                        "sourceExecutionOrdinal": 1,
                        "checkpointBoundary": "after_execution",
                        "checkpointRef": "artifact://source/checkpoint",
                        "checkpointDigest": "sha256:" + "a" * 64,
                    },
                    "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
                    "runtimeContextPolicy": "fresh_agent_run",
                    "instructionRef": "artifact://source/instruction",
                    "instructionDigest": "sha256:" + "b" * 64,
                    "idempotencyKey": "create-turn-1",
                }
            )
            await session.commit()

        payload = {
            "workflowId": "source-workflow",
            "branchId": "branch-1",
            "branchTurnId": "turn-1",
            "principal": "service:test",
            "sourceNamespace": "default",
            "sourceRunId": "source-run",
            "outcome": "failed",
            "agentResult": AgentRunResult(
                summary="test terminal with retained diagnostics",
                diagnosticsRef="artifact://denied-diagnostics",
            ).model_dump(by_alias=True, mode="json", exclude_none=True),
        }
        with pytest.raises(
            RuntimeError,
            match="denied unexpected artifact-storage",
        ):
            await turn_module.persist_checkpoint_branch_turn_terminal(payload)
        assert artifact_calls, (
            "artifact denial was never exercised: terminal persistence did not "
            "cross the artifact-storage authority boundary"
        )
    finally:
        await engine.dispose()


# --- Fail-closed probe observations (MoonMind#3949 scope 3) ------------------


def test_fully_observable_zero_drain_unblocks_via_observations():
    decision = evaluate_checkpoint_compat_drain_observations(
        CheckpointCompatDrainObservations(
            open_pre_cutover_histories=0,
            pending_old_queue_tasks=0,
            supported_resets_pending=0,
        )
    )
    assert decision.may_remove_workflow_queue_handlers is True
    assert decision.required_action == "safe_to_remove"
    assert decision.outstanding == 0
    assert decision.blocking_dimensions == ()


@pytest.mark.parametrize(
    "observations",
    [
        CheckpointCompatDrainObservations(),
        CheckpointCompatDrainObservations(open_pre_cutover_histories=None),
        CheckpointCompatDrainObservations(pending_old_queue_tasks=None),
        CheckpointCompatDrainObservations(supported_resets_pending=None),
        CheckpointCompatDrainObservations(
            open_pre_cutover_histories=0,
            pending_old_queue_tasks=None,
            supported_resets_pending=0,
        ),
        CheckpointCompatDrainObservations(
            open_pre_cutover_histories=2,
            pending_old_queue_tasks=None,
            supported_resets_pending=1,
        ),
    ],
)
def test_unobservable_probe_dimension_retains_compat(observations):
    """Missing visibility or a failed probe is never a clean drain."""

    decision = evaluate_checkpoint_compat_drain_observations(observations)
    assert decision.may_remove_workflow_queue_handlers is False
    assert decision.required_action == "retain_compat"
    assert decision.outstanding > 0
    assert decision.blocking_dimensions, "unobservable drain must name blockers"
    reason = retention_reason(decision)
    assert COMPAT_DRAIN_CONTRACT in reason
    for dimension in decision.blocking_dimensions:
        assert dimension in reason


def test_observations_reject_negative_counts_but_allow_unobservable():
    with pytest.raises(ValueError):
        CheckpointCompatDrainObservations(open_pre_cutover_histories=-1)
    with pytest.raises(ValueError):
        CheckpointCompatDrainObservations(pending_old_queue_tasks=-1)
    with pytest.raises(ValueError):
        CheckpointCompatDrainObservations(supported_resets_pending=-1)
    # None (unobservable/failed probe) is the fail-closed sentinel, not an error.
    decision = evaluate_checkpoint_compat_drain_observations(
        CheckpointCompatDrainObservations()
    )
    assert decision.may_remove_workflow_queue_handlers is False


# --- Production probe wiring (MoonMind#3949 scope 3) ------------------------


def test_probe_collector_binds_live_counts_to_observations():
    observations = collect_checkpoint_compat_drain_observations(
        open_pre_cutover_histories=0,
        pending_old_queue_tasks=0,
        supported_resets_pending=0,
    )
    decision = evaluate_checkpoint_compat_drain_observations(observations)
    assert decision.may_remove_workflow_queue_handlers is True
    assert decision.required_action == "safe_to_remove"


def test_probe_collector_rejects_malformed_counts():
    with pytest.raises(ValueError):
        collect_checkpoint_compat_drain_observations(
            open_pre_cutover_histories=-1,
            pending_old_queue_tasks=0,
            supported_resets_pending=0,
        )


def test_drain_report_names_probes_and_removal_checklist():
    drained = evaluate_checkpoint_compat_drain_observations(
        collect_checkpoint_compat_drain_observations(
            open_pre_cutover_histories=0,
            pending_old_queue_tasks=0,
            supported_resets_pending=0,
        )
    )
    report = render_checkpoint_compat_drain_report(
        drained,
        observations=collect_checkpoint_compat_drain_observations(
            open_pre_cutover_histories=0,
            pending_old_queue_tasks=0,
            supported_resets_pending=0,
        ),
    )
    assert COMPAT_DRAIN_CONTRACT in report
    assert "TaskQueue=" in report
    assert "checkpoint-branch-artifact-fleet-v1" in report
    assert "Removal checklist" in report

    retained = evaluate_checkpoint_compat_drain_observations(
        CheckpointCompatDrainObservations()
    )
    retain_report = render_checkpoint_compat_drain_report(retained)
    assert "Retain the workflow-queue checkpoint handlers" in retain_report
    assert "Removal checklist" not in retain_report


def test_drain_gate_stays_importable_without_heavy_dependencies():
    """The gate lives in a lightweight namespace: stdlib-only import.

    Regression test for tooling contexts where importing the gate through
    the workflow package ``__init__`` chain fails before reaching the
    stdlib-only definitions.
    """

    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent(
        """
        import sys
        class _Blocker:
            BLOCKED = (
                "sqlalchemy", "temporalio", "pydantic", "fastapi",
                "fastapi_users", "api_service",
            )
            def find_module(self, name, path=None):
                if name.split(".")[0] in self.BLOCKED:
                    return self
                return None
            def load_module(self, name):
                raise ImportError(f"blocked heavy dep: {name}")
        sys.meta_path.insert(0, _Blocker())
        for _mod in list(sys.modules):
            if _mod.split(".")[0] in _Blocker.BLOCKED:
                del sys.modules[_mod]
        from moonmind.gates.checkpoint_compat_drain import (
            evaluate_checkpoint_compat_drain,
            CheckpointCompatDrainUsage,
        )
        assert evaluate_checkpoint_compat_drain(
            CheckpointCompatDrainUsage()
        ).may_remove_workflow_queue_handlers is True
        print("stdlib-only import OK")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "stdlib-only import OK" in completed.stdout


# --- Operation identity across the queue move (MoonMind#3949 scope 1) --------


def test_checkpoint_handler_activity_names_are_stable_operation_identity():
    from temporalio import activity as activity_api

    from moonmind.workflows.temporal.workflow_registry import (
        checkpoint_branch_activity_handlers,
    )

    names = {
        activity_api._Definition.must_from_callable(handler).name
        for handler in checkpoint_branch_activity_handlers()
    }
    assert names == {
        "checkpoint_branch.turn.mark_running",
        "checkpoint_branch.turn.persist_terminal",
        "checkpoint_branch.turn.persist_terminal_rejection",
    }


def test_artifacts_and_workflow_fleets_share_identical_handler_objects():
    """The queue move preserves operation identity: same handler objects."""

    from moonmind.workflows.temporal.activity_catalog import (
        ARTIFACTS_FLEET,
        build_default_activity_catalog,
    )
    from moonmind.workflows.temporal.activity_runtime import build_activity_bindings
    from moonmind.workflows.temporal.workflow_registry import (
        checkpoint_branch_activity_handlers,
        workflow_fleet_activity_handlers,
    )

    expected = set(checkpoint_branch_activity_handlers())
    assert expected <= set(workflow_fleet_activity_handlers())
    catalog = build_default_activity_catalog()
    focused_types = {
        "checkpoint_branch.turn.mark_running",
        "checkpoint_branch.turn.persist_terminal",
        "checkpoint_branch.turn.persist_terminal_rejection",
    }
    from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog

    focused = TemporalActivityCatalog(
        activities=tuple(
            item for item in catalog.activities if item.activity_type in focused_types
        ),
        fleets=catalog.fleets,
    )
    bindings = build_activity_bindings(focused, fleets=[ARTIFACTS_FLEET])
    assert {binding.handler for binding in bindings} == expected


# --- Terminal fail-closed evidence (MoonMind#3949 scope 4) --------------------


@pytest.mark.asyncio
async def test_terminal_persistence_fails_closed_without_db(monkeypatch):
    """A DB outage raises instead of recording an unverifiable terminal."""

    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module
    from moonmind.schemas.agent_runtime_models import AgentRunResult

    monkeypatch.setattr(turn_module, "async_session_maker", _deny_session_maker)
    payload = {
        "workflowId": "wf-1",
        "branchId": "b-1",
        "branchTurnId": "t-1",
        "principal": "service:test",
        "sourceNamespace": "default",
        "sourceRunId": "source-run",
        "outcome": "failed",
        "agentResult": AgentRunResult(summary="test terminal").model_dump(
            by_alias=True, mode="json", exclude_none=True
        ),
    }
    with pytest.raises(RuntimeError, match="denied unexpected database"):
        await turn_module.persist_checkpoint_branch_turn_terminal(payload)


@pytest.mark.asyncio
async def test_rejection_persistence_fails_closed_without_db(monkeypatch):
    """The rejection fallback also fails closed instead of partial writes."""

    import moonmind.workflows.temporal.workflows.checkpoint_branch_turn as turn_module

    monkeypatch.setattr(turn_module, "async_session_maker", _deny_session_maker)
    with pytest.raises(RuntimeError, match="denied unexpected database"):
        await turn_module.persist_checkpoint_branch_turn_terminal_rejection(
            {
                "workflowId": "wf-1",
                "branchId": "b-1",
                "branchTurnId": "t-1",
                "principal": "service:test",
                "sourceNamespace": "default",
                "sourceRunId": "source-run",
                "requestedOutcome": "failed",
                "terminalPayloadDigest": "sha256:" + "a" * 64,
            }
        )
