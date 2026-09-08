"""Production-matrix evidence for MoonLadderStudios/MoonMind#3949.

The artifacts-fleet cutover (#4032) is delivered; the workflow-queue
persistence registration stays only for pre-cutover replay/in-flight
compatibility. The verifier's remaining work asks for three recoverable
proofs this module supplies without a Temporal server, deployment probe,
or second worker:

- production-path identity chain: the workflow's scheduled persistence
  strings, the registered handler operation names, and the catalog routes
  agree exactly, every persistence site carries the patch route options
  with the shared retry budget, and per-type call-site timeouts equal the
  serving catalog budgets;
- measured capability inventory: the four retained helpers reference no
  database/credential/Docker/artifact-storage authority in their bodies,
  imports, or env-key strings, while the three persistence handlers
  explicitly carry database authority; the compose spec pins which mounts
  each fleet actually carries;
- service failure matrix: duplicate terminal delivery replays idempotently,
  stale terminal evidence is rejected without overwrite, a failed handoff
  stays visible and unfinalized, and cancellation cannot be overwritten by
  a stale success — all through ``CheckpointBranchService``, the single
  mutator (no second writer).

Out of scope by design (verifier marks them unrecoverable in this
runtime): live deployment drain probes, execution-under-load saturation,
and compat removal itself. Compat stays retained; the drain gate in
``checkpoint_compat_drain`` owns that sequencing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import activity

from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import CheckpointBranchService
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

pytestmark = pytest.mark.unit_fast

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_SRC = (
    REPO_ROOT
    / "moonmind/workflows/temporal/workflows/checkpoint_branch_turn.py"
)
AGENT_RUN_SRC = (
    REPO_ROOT / "moonmind/workflows/temporal/workflows/agent_run.py"
)
COMPOSE_SPEC = REPO_ROOT / "docker-compose.yaml"

PERSISTENCE_TYPES = (
    "checkpoint_branch.turn.mark_running",
    "checkpoint_branch.turn.persist_terminal",
    "checkpoint_branch.turn.persist_terminal_rejection",
)

HELPER_FUNCTIONS = (
    "resolve_adapter_metadata",
    "get_activity_route",
    "resolve_external_adapter",
    "external_adapter_execution_style",
)

HELPER_TYPES = (
    "integration.resolve_adapter_metadata",
    "integration.get_activity_route",
    "integration.resolve_external_adapter",
    "integration.external_adapter_execution_style",
)

PERSISTENCE_HANDLERS = (
    "mark_checkpoint_branch_turn_running",
    "persist_checkpoint_branch_turn_terminal",
    "persist_checkpoint_branch_turn_terminal_rejection",
)

# Authority no workflow helper may reference: database sessions/engines,
# the checkpoint persistence service, container/artifact I/O, or the
# credential-bearing environment keys the workers carry.
FORBIDDEN_HELPER_NAMES = frozenset({
    "async_session_maker",
    "CheckpointBranchService",
    "sqlalchemy",
    "aiosqlite",
    "create_async_engine",
    "docker",
    "TemporalArtifactService",
    "get_checkpoint_branch_artifact_service",
    "OmnigentControlPlaneStore",
    "LocalTemporalArtifactStore",
})

FORBIDDEN_HELPER_ENV_KEYS = frozenset({
    "DATABASE_URL",
    "DOCKER_HOST",
    "DOCKER_SOCKET",
    "MOONMIND_ARTIFACT_STORE_URL",
    "MOONMIND_ARTIFACT_STORAGE_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "MOONMIND_PROVIDER_CREDENTIALS_JSON",
})


def _persistence_schedule_calls() -> dict[str, list[ast.Call]]:
    """Map each scheduled persistence type to its workflow call sites."""

    tree = ast.parse(WORKFLOW_SRC.read_text())
    scheduled: dict[str, list[ast.Call]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute) and func.attr == "execute_activity"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        name = node.args[0].value
        if isinstance(name, str) and name.startswith("checkpoint_branch.turn."):
            scheduled.setdefault(name, []).append(node)
    return scheduled


def _timeout_seconds(call: ast.Call, keyword: str) -> int:
    """Evaluate a ``timedelta(...)`` timeout keyword to whole seconds."""

    for kw in call.keywords:
        if kw.arg != keyword:
            continue
        if not isinstance(kw.value, ast.Call):
            raise AssertionError(f"{keyword} is not a timedelta call")
        parts = {
            sub.arg: sub.value
            for sub in kw.value.keywords
            if isinstance(sub.value, ast.Constant)
        }
        total = (
            parts.get("weeks", 0) * 7 * 24 * 3600
            + parts.get("days", 0) * 24 * 3600
            + parts.get("hours", 0) * 3600
            + parts.get("minutes", 0) * 60
            + parts.get("seconds", 0)
        )
        assert total > 0, f"{keyword} must be a positive timeout"
        return int(total)
    raise AssertionError(f"{keyword} missing from persistence call site")


def _function_body_names(source: Path, function_name: str) -> set[str]:
    """Collect Name/Attribute roots referenced inside one function body."""

    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == function_name
        ):
            names: set[str] = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name):
                    names.add(child.id)
                elif isinstance(child, ast.Attribute):
                    # Collect the attribute itself (e.g. lock_turn_execution
                    # in service.lock_turn_execution) as well as its root.
                    names.add(child.attr)
                    cursor = child
                    while isinstance(cursor, ast.Attribute):
                        cursor = cursor.value
                    if isinstance(cursor, ast.Name):
                        names.add(cursor.id)
            return names
    raise AssertionError(f"{function_name} not found in {source}")


def _function_body_strings(source: Path, function_name: str) -> set[str]:
    """Collect string constants referenced inside one function body."""

    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == function_name
        ):
            return {
                child.value
                for child in ast.walk(node)
                if isinstance(child, ast.Constant)
                and isinstance(child.value, str)
            }
    raise AssertionError(f"{function_name} not found in {source}")


# --- 1. Production-path identity chain ---------------------------------------


def test_scheduled_registered_and_routed_persistence_agree_exactly():
    """One identity chain: schedule strings == handler names == routes."""

    scheduled = set(_persistence_schedule_calls())
    registered = {
        activity._Definition.must_from_callable(handler).name
        for handler in checkpoint_branch_activity_handlers()
    }
    catalog = build_default_activity_catalog()
    routed = {
        item.activity_type
        for item in catalog.activities
        if item.activity_type in scheduled
    }
    assert scheduled == set(PERSISTENCE_TYPES)
    assert registered == set(PERSISTENCE_TYPES)
    assert routed == set(PERSISTENCE_TYPES)
    for activity_type in PERSISTENCE_TYPES:
        route = catalog.resolve_activity(activity_type)
        assert route.fleet == ARTIFACTS_FLEET
        assert route.task_queue == ARTIFACTS_TASK_QUEUE


def test_persistence_binds_only_on_the_artifacts_fleet():
    """No other fleet can serve the new persistence writes."""

    from moonmind.workflows.temporal.activity_catalog import (
        TemporalActivityCatalog,
    )

    catalog = build_default_activity_catalog()
    focused = TemporalActivityCatalog(
        activities=tuple(
            item
            for item in catalog.activities
            if item.activity_type in PERSISTENCE_TYPES
        ),
        fleets=catalog.fleets,
    )
    expected_handlers = set(checkpoint_branch_activity_handlers())
    bindings = build_activity_bindings(focused, fleets=[ARTIFACTS_FLEET])
    assert {binding.handler for binding in bindings} == expected_handlers
    assert all(binding.task_queue == ARTIFACTS_TASK_QUEUE for binding in bindings)
    for fleet in catalog.fleets:
        if fleet.fleet == ARTIFACTS_FLEET:
            continue
        assert build_activity_bindings(focused, fleets=[fleet.fleet]) == (), (
            f"{fleet.fleet} must not serve checkpoint persistence"
        )
    assert {fleet.fleet for fleet in catalog.fleets} >= {
        ARTIFACTS_FLEET,
        LLM_FLEET,
        SANDBOX_FLEET,
        INTEGRATIONS_FLEET,
        AGENT_RUNTIME_FLEET,
    }


def test_every_persistence_site_carries_route_options_and_shared_retry():
    """Success, failure, cancellation, and retry paths share one budget."""

    from moonmind.workflows.temporal.workflows import (
        checkpoint_branch_turn as turn_module,
    )

    assert turn_module._RETRY.maximum_attempts == 3
    scheduled = _persistence_schedule_calls()
    assert set(scheduled) == set(PERSISTENCE_TYPES)
    for activity_type, calls in scheduled.items():
        for call in calls:
            dumped = ast.dump(call)
            assert "_persistence_route_options" in dumped or (
                "activity_options" in dumped
            ), f"{activity_type} schedules without patch route options"
            assert any(kw.arg is None for kw in call.keywords), (
                f"{activity_type} must spread the route options"
            )
            retry = next(
                (kw for kw in call.keywords if kw.arg == "retry_policy"), None
            )
            assert retry is not None, f"{activity_type} must set retry_policy"
            assert isinstance(retry.value, ast.Name), (
                f"{activity_type} must use the shared retry budget"
            )
            assert retry.value.id == "_RETRY"


def test_call_site_timeouts_equal_the_serving_catalog_budgets():
    """The workflow's recorded timeouts match what the fleet actually serves."""

    catalog = build_default_activity_catalog()
    scheduled = _persistence_schedule_calls()
    assert set(scheduled) == set(PERSISTENCE_TYPES)
    for activity_type, calls in scheduled.items():
        route = catalog.resolve_activity(activity_type)
        for call in calls:
            assert _timeout_seconds(call, "start_to_close_timeout") == (
                route.timeouts.start_to_close_seconds
            ), activity_type
            assert _timeout_seconds(call, "schedule_to_close_timeout") == (
                route.timeouts.schedule_to_close_seconds
            ), activity_type


def test_cancellation_reaches_persistence_only_through_the_routed_path():
    """The ABANDON shield delegates; it never schedules persistence itself."""

    source = WORKFLOW_SRC.read_text()
    block = source.split("async def _persist_cancellation_terminal")[1].split(
        "@workflow.run"
    )[0]
    assert "self._persist_terminal(" in block
    assert "execute_activity" not in block
    assert "ActivityCancellationType.ABANDON" in source
    assert "asyncio.shield(terminal_task)" in source


# --- 2. Measured capability inventory ----------------------------------------


def test_retained_helpers_reference_no_io_authority():
    """Measured call graph: helpers cannot reach DB/credential/Docker I/O."""

    for helper in HELPER_FUNCTIONS:
        names = _function_body_names(AGENT_RUN_SRC, helper)
        assert not (names & FORBIDDEN_HELPER_NAMES), (
            f"{helper} references I/O authority: {names & FORBIDDEN_HELPER_NAMES}"
        )
        strings = _function_body_strings(AGENT_RUN_SRC, helper)
        assert not (strings & FORBIDDEN_HELPER_ENV_KEYS), (
            f"{helper} reads credential env: {strings & FORBIDDEN_HELPER_ENV_KEYS}"
        )


def test_helper_module_imports_carry_no_io_authority():
    """The helpers' module cannot import persistence I/O either."""

    tree = ast.parse(AGENT_RUN_SRC.read_text())
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not (roots & {"sqlalchemy", "aiosqlite", "docker", "api_service"}), (
        f"helper module imports I/O authority: {roots}"
    )


def test_persistence_handlers_explicitly_carry_database_authority():
    """The split is explicit: persistence handlers own DB sessions."""

    for handler in PERSISTENCE_HANDLERS:
        names = _function_body_names(WORKFLOW_SRC, handler)
        assert "async_session_maker" in names, (
            f"{handler} must carry database authority explicitly"
        )
    for handler in PERSISTENCE_HANDLERS[1:]:
        names = _function_body_names(WORKFLOW_SRC, handler)
        assert "lock_turn_execution" in names, (
            f"{handler} must fence on the locked turn execution"
        )


def test_workflow_fleet_composition_is_helpers_plus_compat_persistence():
    """Registry composition pins the four helpers plus three compat handlers."""

    names = {
        activity._Definition.must_from_callable(handler).name
        for handler in workflow_fleet_activity_handlers()
    }
    assert names == set(HELPER_TYPES) | set(PERSISTENCE_TYPES)


def test_deployment_spec_pins_each_fleet_mount_boundary():
    """Measured mounts: workflow fleet carries workspaces, artifacts does not."""

    import yaml

    compose = yaml.safe_load(COMPOSE_SPEC.read_text())
    workflow_worker = compose["services"]["temporal-worker-workflow"]
    artifacts_worker = compose["services"]["temporal-worker-artifacts"]

    def env_value(service: dict, key: str) -> str:
        for entry in service["environment"]:
            if isinstance(entry, str) and entry.startswith(f"{key}="):
                return entry.split("=", 1)[1]
        raise AssertionError(f"{key} missing from worker spec")

    assert env_value(workflow_worker, "TEMPORAL_WORKER_FLEET") == "workflow"
    assert env_value(artifacts_worker, "TEMPORAL_WORKER_FLEET") == "artifacts"

    workflow_volumes = list(workflow_worker.get("volumes", []))
    artifacts_volumes = list(artifacts_worker.get("volumes", []))
    assert any(str(volume).startswith("agent_workspaces:") for volume in workflow_volumes)
    assert not any(
        str(volume).startswith("agent_workspaces:") for volume in artifacts_volumes
    ), "artifacts fleet must not mount agent workspaces"
    assert any("moonmind_secrets" in str(volume) for volume in workflow_volumes)
    assert any("moonmind_secrets" in str(volume) for volume in artifacts_volumes)
    # The retained workflow-queue handlers still need artifact authority, so
    # the consolidated workflow worker intentionally carries the S3 backend
    # config; queue separation is not privilege separation.
    assert "TEMPORAL_ARTIFACT_S3_ENDPOINT" in "".join(
        str(entry) for entry in workflow_worker["environment"]
    )


# --- 3. Service failure matrix (single mutator) -------------------------------


@pytest_asyncio.fixture()
async def matrix_session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/matrix.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="wf-matrix",
                run_id="run-matrix",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


def _matrix_branch(branch_id: str) -> dict:
    return {
        "branchId": branch_id,
        "source": {
            "workflowId": "wf-matrix",
            "runId": "run-matrix",
            "logicalStepId": "implement",
            "sourceExecutionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoint/matrix-source",
            "checkpointDigest": "sha256:matrix-source",
        },
        "label": "Matrix turn",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "runtimeContextPolicy": "fresh_agent_run",
        "gitRepository": "repo://moonmind",
        "gitBaseBranch": "main",
        "gitBaseCommit": "abc123",
        "gitWorkBranch": f"mm/wf-matrix/implement/{branch_id}",
        "createdBy": "MM-3949",
        "instructionRef": f"artifact://instructions/{branch_id}",
        "instructionDigest": "sha256:matrix-instruction",
        "idempotencyKey": f"MM-3949:{branch_id}:create",
    }


async def _claimed_turn(service: CheckpointBranchService, branch_id: str) -> str:
    from api_service.services.checkpoint_branch_service import (
        build_branch_turn_launch_idempotency_key,
    )

    graph = await service.create_branch_graph(_matrix_branch(branch_id))
    turn_id = graph.turns[0].branch_turn_id
    await service.claim_turn_execution(
        workflow_id="wf-matrix",
        branch_id=branch_id,
        branch_turn_id=turn_id,
        context_bundle_ref=f"artifact://context/{branch_id}",
        step_execution_manifest_ref=f"artifact://manifest/{branch_id}",
        diagnostics_ref=f"artifact://diagnostics/{branch_id}",
        agent_request_ref=f"artifact://agent-request/{branch_id}",
        created_step_execution_id="wf-matrix:run-branch:implement:execution:4",
        runtime_agent_run_id=f"checkpoint-branch-agent:{branch_id}:turn-1",
        launch_idempotency_key=build_branch_turn_launch_idempotency_key(
            workflow_id="wf-matrix",
            branch_id=branch_id,
            branch_turn_id=turn_id,
        ),
        execution_workflow_id=f"checkpoint-branch-turn:{turn_id}",
    )
    await service.mark_turn_running(
        workflow_id="wf-matrix",
        branch_id=branch_id,
        branch_turn_id=turn_id,
        runtime_agent_run_id=f"checkpoint-branch-agent:{branch_id}:turn-1",
    )
    return turn_id


def _finalize_kwargs(branch_id: str, suffix: str) -> dict:
    return {
        "workflow_id": "wf-matrix",
        "branch_id": branch_id,
        "outcome": "succeeded",
        "agent_result_ref": f"artifact://agent-result/{branch_id}-{suffix}",
        "diagnostics_ref": f"artifact://terminal-diagnostics/{branch_id}-{suffix}",
        "checkpoint_ref": f"artifact://checkpoint/{branch_id}-{suffix}",
        "checkpoint_digest": f"sha256:checkpoint-{branch_id}-{suffix}",
        "provider_session_id": f"omnigent-session-{branch_id}",
    }


@pytest.mark.asyncio
async def test_duplicate_terminal_delivery_replays_without_overwrite(
    matrix_session: AsyncSession,
) -> None:
    """Duplicate delivery (retry/restart) is idempotent, not a second write."""

    service = CheckpointBranchService(matrix_session)
    branch_id = "cbr-matrix-duplicate"
    turn_id = await _claimed_turn(service, branch_id)

    first = await service.finalize_turn_execution(
        branch_turn_id=turn_id, **_finalize_kwargs(branch_id, "t1")
    )
    branch = await matrix_session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    version_after_first = branch.current_head_version

    second = await service.finalize_turn_execution(
        branch_turn_id=turn_id, **_finalize_kwargs(branch_id, "t1")
    )
    await matrix_session.commit()

    assert second.diagnostics["agentResultRef"] == (
        first.diagnostics["agentResultRef"]
    )
    await matrix_session.expire_all()
    branch = await matrix_session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    assert branch.current_head_version == version_after_first
    assert branch.current_head_checkpoint_ref == (
        f"artifact://checkpoint/{branch_id}-t1"
    )


@pytest.mark.asyncio
async def test_stale_terminal_evidence_is_rejected_not_overwritten(
    matrix_session: AsyncSession,
) -> None:
    """A stale generation cannot overwrite newer terminal evidence."""

    service = CheckpointBranchService(matrix_session)
    branch_id = "cbr-matrix-stale"
    turn_id = await _claimed_turn(service, branch_id)
    await service.finalize_turn_execution(
        branch_turn_id=turn_id, **_finalize_kwargs(branch_id, "t1")
    )

    with pytest.raises(ValueError, match="immutable terminal field"):
        await service.finalize_turn_execution(
            branch_turn_id=turn_id, **_finalize_kwargs(branch_id, "t2")
        )
    await matrix_session.expire_all()

    turn = await matrix_session.get(WorkflowCheckpointBranchTurn, turn_id)
    assert turn is not None
    assert turn.diagnostics["agentResultRef"] == (
        f"artifact://agent-result/{branch_id}-t1"
    )
    branch = await matrix_session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    assert branch.current_head_checkpoint_ref == (
        f"artifact://checkpoint/{branch_id}-t1"
    )


@pytest.mark.asyncio
async def test_failed_handoff_stays_visible_and_unfinalized(
    matrix_session: AsyncSession,
) -> None:
    """No finalize means no terminal: the handoff stays visible for retry."""

    service = CheckpointBranchService(matrix_session)
    branch_id = "cbr-matrix-failed-handoff"
    turn_id = await _claimed_turn(service, branch_id)
    await matrix_session.commit()
    await matrix_session.expire_all()

    turn = await matrix_session.get(WorkflowCheckpointBranchTurn, turn_id)
    assert turn is not None
    assert turn.status == "running"
    assert "agentResultRef" not in (turn.diagnostics or {})
    assert (turn.diagnostics or {}).get("verificationPending") is True
    branch = await matrix_session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    assert branch.current_head_checkpoint_ref == (
        "artifact://checkpoint/matrix-source"
    )
    assert "latestBranchTurnResult" not in (branch.artifact_refs or {})


@pytest.mark.asyncio
async def test_cancellation_cannot_be_overwritten_by_stale_success(
    matrix_session: AsyncSession,
) -> None:
    """Cancellation terminalizes first; a late success loses the race."""

    service = CheckpointBranchService(matrix_session)
    branch_id = "cbr-matrix-cancel"
    turn_id = await _claimed_turn(service, branch_id)
    canceled = await service.finalize_turn_execution(
        workflow_id="wf-matrix",
        branch_id=branch_id,
        branch_turn_id=turn_id,
        outcome="canceled",
        agent_result_ref=f"artifact://agent-result/{branch_id}-cancel",
        diagnostics_ref=f"artifact://terminal-diagnostics/{branch_id}-cancel",
        provider_session_id=f"omnigent-session-{branch_id}",
    )
    assert canceled.status == "canceled"
    assert canceled.diagnostics["verificationPending"] is False

    with pytest.raises(ValueError, match="immutable terminal field"):
        await service.finalize_turn_execution(
            branch_turn_id=turn_id, **_finalize_kwargs(branch_id, "late")
        )
    await matrix_session.expire_all()

    turn = await matrix_session.get(WorkflowCheckpointBranchTurn, turn_id)
    assert turn is not None
    assert turn.status == "canceled"
    branch = await matrix_session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    assert branch.current_head_checkpoint_ref == (
        "artifact://checkpoint/matrix-source"
    )


@pytest.mark.asyncio
async def test_restarted_worker_cannot_steal_running_turn_or_release_cleanup(
    matrix_session: AsyncSession,
) -> None:
    """A restarted worker replays the running handoff without stealing it.

    The new ``CheckpointBranchService`` handle on the same durable state
    cannot claim the turn under a different AgentRun identity, the turn
    stays running under its original owner, the branch head does not
    advance, and no terminal result is published (cleanup authority stays
    with the running handoff).
    """

    service = CheckpointBranchService(matrix_session)
    branch_id = "cbr-matrix-restart"
    turn_id = await _claimed_turn(service, branch_id)
    await matrix_session.commit()

    restarted = CheckpointBranchService(matrix_session)
    with pytest.raises(ValueError, match="does not match claim"):
        await restarted.mark_turn_running(
            workflow_id="wf-matrix",
            branch_id=branch_id,
            branch_turn_id=turn_id,
            runtime_agent_run_id="checkpoint-branch-agent:restarted:turn-1",
        )
    await matrix_session.expire_all()

    turn = await matrix_session.get(WorkflowCheckpointBranchTurn, turn_id)
    assert turn is not None
    assert turn.status == "running"
    assert turn.runtime_agent_run_id == f"checkpoint-branch-agent:{branch_id}:turn-1"
    assert turn.completed_at is None
    assert turn.diagnostics["deliveryStage"] == "running"
    assert "agentResultRef" not in (turn.diagnostics or {})
    branch = await matrix_session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    assert branch.current_head_checkpoint_ref == "artifact://checkpoint/matrix-source"
    assert "latestBranchTurnResult" not in (branch.artifact_refs or {})


@pytest.mark.asyncio
async def test_rejection_terminalizes_blocked_without_advancing_head(
    matrix_session: AsyncSession,
) -> None:
    """The rejection fallback terminalizes as blocked without a head advance.

    This is the service-level ordering half of the end-to-end rejection
    flow (the Temporal half lives in
    ``test_new_rejection_reaches_artifacts_fleet_with_real_handlers_3949``):
    a failed handoff that falls back to the rejection path records
    ``retained_evidence_rejected``, never publishes a terminal checkpoint
    for the turn, and a delayed divergent terminal afterwards is rejected
    instead of overwriting the rejection.
    """

    service = CheckpointBranchService(matrix_session)
    branch_id = "cbr-matrix-rejection"
    turn_id = await _claimed_turn(service, branch_id)

    rejected = await service.finalize_turn_execution(
        workflow_id="wf-matrix",
        branch_id=branch_id,
        branch_turn_id=turn_id,
        outcome="blocked",
        agent_result_ref=f"artifact://agent-result/{branch_id}-rejection",
        diagnostics_ref=f"artifact://terminal-diagnostics/{branch_id}-rejection",
        terminal_disposition="retained_evidence_rejected",
    )
    assert rejected.status == "blocked"
    assert rejected.diagnostics["terminalDisposition"] == "retained_evidence_rejected"
    assert rejected.diagnostics["verificationPending"] is False
    await matrix_session.commit()
    await matrix_session.expire_all()

    branch = await matrix_session.get(WorkflowCheckpointBranch, branch_id)
    assert branch is not None
    assert branch.current_head_checkpoint_ref == "artifact://checkpoint/matrix-source"
    assert "latestBranchTurnCheckpoint" not in (branch.artifact_refs or {})

    with pytest.raises(ValueError, match="immutable terminal field"):
        await service.finalize_turn_execution(
            branch_turn_id=turn_id, **_finalize_kwargs(branch_id, "late")
        )
    await matrix_session.expire_all()
    turn = await matrix_session.get(WorkflowCheckpointBranchTurn, turn_id)
    assert turn is not None
    assert turn.status == "blocked"
    assert turn.diagnostics["agentResultRef"] == (
        f"artifact://agent-result/{branch_id}-rejection"
    )
