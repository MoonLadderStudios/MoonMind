"""Producer-level routing into the canonical turn boundary.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

``tests/unit/omnigent/test_canonical_turn_routing.py`` proves the boundary
behaves correctly once it is handed a source. Every source value there is
supplied by the test, so it cannot catch a *producer* that reaches the provider
under the wrong source -- or under no claimed command at all.

These tests assert the routing instead: an admitted request is dispatched
through the real production realizer against a real control-plane store, and the
durable turn journal is read back. Nothing here tells the production code which
source to use.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.control_plane import (
    CLEANUP_STATE_COMPLETE,
    OmnigentControlPlaneStore,
    TurnSource,
)
from moonmind.omnigent.control_plane.turn_admission import (
    RemediationAuthorityBroadenedError,
)
from moonmind.omnigent.control_plane.identities import (
    canonical_omnigent_session_id,
)
from moonmind.omnigent.control_plane.turn_commands import CanonicalTurnCommandService
from moonmind.omnigent.harness_platform.agent_profile import OmnigentAgentProfileV2
from moonmind.omnigent.harness_platform.catalog import (
    HarnessImplementationIdentity,
    TrustState,
    classify_harness_trust,
    create_catalog_snapshot,
)
from moonmind.omnigent.harness_platform.credential_bindings import create_binding_set
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import HostClass
from moonmind.omnigent.harness_platform.planner import compile_execution_plan
from moonmind.omnigent.harness_platform.skills import ResolvedSkillSet
from moonmind.omnigent.harness_platform.support import KNOWN_REALIZERS
from moonmind.omnigent.realizers.codex_profile_bound import CodexProfileBoundRealizer
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.omnigent.realizers.turn_delivery import canonical_turn_source
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.remediation_loop import (
    ConsumedRemediationBudgets,
    RemediationLoopPhase,
    RemediationLoopSpec,
    RemediationLoopState,
    materialize_attempt_nodes,
)
from moonmind.workflows.temporal.remediation_workspace_head import (
    RemediationWorkspaceHead,
)
from moonmind.workflows.temporal.workflows.run import (
    RUN_CANONICAL_TURN_LINEAGE_PATCH,
    RUN_OMNIGENT_EXECUTION_PLAN_REF_PATCH,
    MoonMindRunWorkflow,
)


PROVIDER_PROFILE_REF = "codex-profile-1"

_CONTROL_PLANE_TABLES = (
    "omnigent_chat_binding_aliases",
    "omnigent_cleanup_authority",
    "omnigent_reconciliation_decisions",
    "omnigent_commands",
    "omnigent_observations",
    "omnigent_turn_attempts",
    "omnigent_sessions",
)


@pytest_asyncio.fixture(scope="module")
async def _engine(tmp_path_factory):
    root = tmp_path_factory.mktemp("turn_producers")
    engine = create_async_engine(f"sqlite+aiosqlite:///{root}/turn_producers.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def session_factory(_engine):
    async with _engine.begin() as conn:
        for table in _CONTROL_PLANE_TABLES:
            await conn.execute(text(f"DELETE FROM {table}"))
    return sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture()
async def turn_commands(session_factory):
    return CanonicalTurnCommandService(OmnigentControlPlaneStore(session_factory))


# --- Real admitted Codex execution plan --------------------------------------


def _codex_execution_plan():
    """Compile the same immutable plan the trusted planner produces for Codex."""

    digest = "sha256:" + "e" * 64
    catalog = create_catalog_snapshot(
        endpointRef="default",
        omnigentVersion="1.0.0",
        omnigentBuildDigest="sha256:" + "b" * 64,
        sourceDigest="sha256:" + "c" * 64,
        harnesses=[
            {
                "id": "codex-native",
                "aliases": [],
                "label": "codex-native",
                "implementation": {
                    "sourceKind": "core",
                    "package": "omnigent",
                    "version": "1.0.0",
                    "digest": digest,
                    "pluginEntryPoint": None,
                },
                "runtimeRequirements": {},
                "capabilities": {
                    "integrationMode": "native-server",
                    "authModel": "own-auth",
                    "interrupt": True,
                    "streaming": True,
                },
                "setupSteps": [],
            }
        ],
        observedAt=datetime.now(UTC),
    )
    impl = HarnessImplementationIdentity.model_validate(
        {
            "sourceKind": "core",
            "package": "omnigent",
            "version": "1.0.0",
            "digest": digest,
            "pluginEntryPoint": None,
        }
    )
    trust = classify_harness_trust(
        harnessId="codex-native",
        implementation=impl,
        trustState=TrustState.core_trusted,
    )
    profile = OmnigentAgentProfileV2.model_validate(
        {
            "schemaVersion": "moonmind.omnigent-agent-profile.v2",
            "endpointRef": "default",
            "source": {
                "kind": "upstream",
                "upstreamId": "codex-native-ui",
                "upstreamVersion": "1.0.0",
                "upstreamSnapshotDigest": "sha256:" + "d" * 64,
            },
            "harness": {
                "id": "codex-native",
                "catalogRef": catalog.catalogRef,
                "implementationRef": impl.implementation_ref(),
            },
            "requirements": {
                "harness": {"required": []},
                "moonmind": {"required": []},
                "host": {"required": []},
            },
            "credentialSlots": [
                {
                    "id": "primary-model",
                    "optional": False,
                    "acceptedAuthModels": ["oauth_volume"],
                    "acceptedProviderIds": ["openai"],
                }
            ],
            "model": {},
            "workspace": {},
            "skills": [],
            "tools": [],
            "capture": {},
            "continuations": {},
            "publish": {},
            "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
        }
    )
    host_class = HostClass.model_validate(
        {
            "hostClassId": "omnigent-codex-current",
            "version": 1,
            "imageRef": "ghcr.io/example/codex-host@sha256:" + "d" * 64,
            "omnigentVersion": "1.0.0",
            "omnigentBuildDigest": "sha256:" + "b" * 64,
            "architectures": ["linux/amd64"],
            "declaredHarnessImplementations": [
                {
                    "harnessId": "codex-native",
                    "implementationRef": impl.implementation_ref(),
                    "runtimeDependencies": [],
                }
            ],
            "integrationModes": ["native-server"],
            "materializerRefs": ["codex-oauth-home@1"],
            "features": {
                "readOnlyRoot": True,
                "restrictedEgress": True,
                "workspaceBind": True,
            },
            "runtime": {"uid": 1000, "gid": 1000, "home": "/home/app"},
        }
    )
    plan = compile_execution_plan(
        agent_profile=profile,
        harness_catalog=catalog,
        trust_record=trust,
        resolved_skills=ResolvedSkillSet.model_validate(
            {
                "resolvedSkillSetRef": "artifact:test",
                "resolvedSkillSetDigest": "sha256:" + "a" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "b" * 64,
            }
        ),
        credential_binding_set=create_binding_set(
            bindingSetId="codex",
            version=1,
            bindings={
                "primary-model": {
                    "providerProfileRef": PROVIDER_PROFILE_REF,
                    "materializerRef": "codex-oauth-home@1",
                }
            },
        ),
        host_class_ref="omnigent-codex-current@1",
        host_class=host_class,
        launch_policy_ref="codex-on-demand@1",
        model_qualified_id="gpt-5",
        model_effort=None,
        model_route_ref="openai",
        model_normalized_options={},
    )
    assert plan.payload.executionRealizerRef == CodexProfileBoundRealizer.ref
    return plan


@pytest.fixture(scope="module")
def plan():
    return _codex_execution_plan()


#: The one production run workflow whose dispatch loop launches remediation
#: attempts. These identities are the ones ``workflows/run.py`` derives from
#: ``workflow.info()``; nothing here hand-builds a canonical turn source.
WORKFLOW_ID = "wf-canonical-turn"
TEMPORAL_RUN_ID = "run-canonical-turn"
LOOP_ID = "issue-implementation-remediation"
BASE_LOGICAL_STEP_ID = "implement-the-issue"
BASE_STEP_EXECUTION_ID = (
    f"{WORKFLOW_ID}:{TEMPORAL_RUN_ID}:{BASE_LOGICAL_STEP_ID}:execution:1"
)


class _MockWorkflowInfo:
    workflow_id = WORKFLOW_ID
    run_id = TEMPORAL_RUN_ID


def _loop_spec() -> RemediationLoopSpec:
    """The authored remediation-loop contract a run workflow admits."""

    return RemediationLoopSpec.model_validate(
        {
            "kind": "remediation_loop",
            "loopId": LOOP_ID,
            "remediationTool": {
                "type": "skill",
                "name": "auto",
                "inputs": {"instructions": "Fix the remaining verified gaps."},
            },
            "verificationTool": {
                "type": "skill",
                "name": "moonspec-verify",
                "inputs": {"instructions": "Verify the remediated candidate."},
            },
            "workspacePolicy": "continue_from_loop_head",
            "budgets": {"hardMaxAttempts": 3},
            "terminalPolicy": {
                "fullyImplemented": "advance",
                "additionalWorkNeeded": "continue_when_allowed",
                "blocked": "stop",
                "noDetermination": "retry_evidence_or_stop",
                "failedUnrecoverable": "stop",
            },
            "sideEffectPolicy": "workflow_owned",
            "publicationPolicy": "evaluate_after_terminal",
        }
    )


def _loop_head(*, base_step_execution_id: str | None) -> RemediationWorkspaceHead:
    """The workflow-owned loop head naming the attempt a remediation repairs."""

    return RemediationWorkspaceHead(
        loopId=LOOP_ID,
        branchRef=f"checkpoint-branch:{LOOP_ID}",
        rootCheckpointRef="artifact://workspace/C0",
        rootWorkspaceDigest="sha256:" + "a" * 64,
        headCheckpointRef="artifact://workspace/C0",
        headWorkspaceDigest="sha256:" + "a" * 64,
        headStepExecutionId=base_step_execution_id,
        headAttemptOrdinal=0,
        headVersion=1,
    )


def _run_workflow(
    *, attempt_ordinal: int = 1, base_step_execution_id: str | None
) -> MoonMindRunWorkflow:
    """A run workflow with the loop state its controller actually owns."""

    wf = MoonMindRunWorkflow()
    wf._remediation_loop_spec = _loop_spec()
    wf._remediation_loop_state = RemediationLoopState(
        loopId=LOOP_ID,
        attemptOrdinal=attempt_ordinal,
        phase=RemediationLoopPhase.REMEDIATION_RUNNING,
        consumedBudgets=ConsumedRemediationBudgets(attempts=attempt_ordinal),
    )
    wf._remediation_workspace_head = _loop_head(
        base_step_execution_id=base_step_execution_id
    )
    return wf


def _base_node(*, publish_mode: str) -> dict:
    """An ordinary authored Omnigent step -- the attempt remediation repairs."""

    return {
        "id": BASE_LOGICAL_STEP_ID,
        "title": "Implement the issue",
        "tool": {"type": "agent_runtime", "name": "omnigent"},
        "inputs": {
            "instructions": "Implement the issue.",
            "runtime": {
                "mode": "omnigent",
                "executionProfileRef": PROVIDER_PROFILE_REF,
            },
            "repositoryOperation": "write",
            "publishMode": publish_mode,
        },
    }


def _remediation_node(*, ordinal: int, publish_mode: str) -> dict:
    """The attempt node the production loop materializer compiles."""

    remediation, _verification = materialize_attempt_nodes(
        spec=_loop_spec(),
        workflow_id=WORKFLOW_ID,
        run_id=TEMPORAL_RUN_ID,
        ordinal=ordinal,
        workspace_head_ref="artifact://loop-head/1",
        runtime={"mode": "omnigent", "executionProfileRef": PROVIDER_PROFILE_REF},
        remediation_inputs={"publishMode": publish_mode},
    )
    return remediation


def _dispatch_request(
    wf: MoonMindRunWorkflow, node: dict, *, plan_ref: str
) -> AgentExecutionRequest:
    """Build the request exactly as the run workflow's dispatch loop does.

    ``_record_canonical_turn_lineage`` is the controller attestation the
    dispatch loop performs immediately before building the request; nothing in
    this test names a turn source or hand-builds remediation authority.
    """

    node_id = str(node["id"])
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_MockWorkflowInfo(),
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda patch_id: patch_id
        in {
            RUN_OMNIGENT_EXECUTION_PLAN_REF_PATCH,
            RUN_CANONICAL_TURN_LINEAGE_PATCH,
        },
    ):
        wf._record_canonical_turn_lineage(node=node, node_id=node_id)
        return wf._build_agent_execution_request(
            node_inputs=dict(node["inputs"]),
            node_id=node_id,
            tool_name="omnigent",
            workflow_parameters={"executionPlanRef": plan_ref},
            step_execution=1,
        )


def _request(
    *,
    plan,
    correlation_id: str,
    idempotency_key: str,
    publish_mode: str = "none",
) -> AgentExecutionRequest:
    """A minimal admitted request for boundary cases with no Step Execution."""

    return AgentExecutionRequest.model_validate(
        {
            "agentKind": "external",
            "agentId": "omnigent",
            "correlationId": correlation_id,
            "idempotencyKey": idempotency_key,
            "executionProfileRef": PROVIDER_PROFILE_REF,
            "parameters": {
                "executionPlanRef": plan.planRef,
                "publishMode": publish_mode,
            },
        }
    )


class _RecordingCoordinator:
    """Stand in for the Codex lifecycle without replacing turn ownership."""

    def __init__(self, **_kwargs) -> None:
        self.requests: list[AgentExecutionRequest] = []

    async def execute(self, request: AgentExecutionRequest) -> AgentRunResult:
        self.requests.append(request)
        return AgentRunResult(
            summary="codex lifecycle ran",
            metadata={"omnigentSessionId": "provider-session-1"},
        )


def _realizer(turn_commands, session_factory):
    lifecycles: list[_RecordingCoordinator] = []

    def factory(**kwargs):
        coordinator = _RecordingCoordinator(**kwargs)
        lifecycles.append(coordinator)
        return coordinator

    realizer = CodexProfileBoundRealizer(
        session_factory=session_factory,
        coordinator_factory=factory,
        turn_command_service=turn_commands,
    )
    return realizer, lifecycles


def _session_id(correlation_id: str) -> str:
    """The canonical session identity a request with no Step Execution gets."""

    return canonical_omnigent_session_id(
        workflow_id=correlation_id,
        step_execution_id=correlation_id,
        agent_run_id=correlation_id,
    )


def _session_id_for(request: AgentExecutionRequest) -> str:
    """The canonical session identity the realizer boundary derives itself."""

    step_execution = request.step_execution
    if step_execution is None:
        return _session_id(request.correlation_id)
    return canonical_omnigent_session_id(
        workflow_id=step_execution.workflow_id,
        step_execution_id=step_execution.step_execution_id,
        agent_run_id=request.correlation_id,
    )


async def _turn_journal(session_factory, session_id: str):
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(session_id)
        commands = await repos.commands.list_for_session(session_id)
        cleanup = await repos.cleanup.get(session_id)
    return turns, commands, cleanup


# --- The launching controller is the only source authority -------------------


def test_turn_source_is_derived_from_controller_attested_lineage(plan) -> None:
    """Only workflow-owned loop state makes a dispatch a REMEDIATION turn."""

    base = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _base_node(publish_mode="none"),
        plan_ref=plan.planRef,
    )
    remediation = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _remediation_node(ordinal=1, publish_mode="none"),
        plan_ref=plan.planRef,
    )

    assert canonical_turn_source(base) is TurnSource.INITIAL
    assert canonical_turn_source(remediation) is TurnSource.REMEDIATION
    # The source is the controller's attestation, not the dead remediation
    # workspace seam: no production dispatch populates that field.
    assert base.remediation_workspace is None
    assert remediation.remediation_workspace is None
    lineage = remediation.step_execution.canonical_turn_lineage
    assert lineage is not None
    assert lineage.base_step_execution_id == BASE_STEP_EXECUTION_ID


def test_a_forged_remediation_node_without_loop_state_gets_no_lineage(plan) -> None:
    """Plan annotations alone cannot claim remediation authority."""

    wf = MoonMindRunWorkflow()
    wf._remediation_loop_state = None
    request = _dispatch_request(
        wf,
        _remediation_node(ordinal=1, publish_mode="none"),
        plan_ref=plan.planRef,
    )

    assert request.step_execution.canonical_turn_lineage is None
    assert canonical_turn_source(request) is TurnSource.INITIAL


# --- RW-5: the production remediation producer -------------------------------


@pytest.mark.asyncio
async def test_remediation_dispatched_through_the_realizer_journals_remediation(
    turn_commands, session_factory, plan
) -> None:
    """A production remediation attempt persists lineage_kind='remediation'."""

    realizer, lifecycles = _realizer(turn_commands, session_factory)
    base = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _base_node(publish_mode="none"),
        plan_ref=plan.planRef,
    )
    await realizer.execute(base, plan)

    request = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _remediation_node(ordinal=1, publish_mode="none"),
        plan_ref=plan.planRef,
    )
    result = await realizer.execute(request, plan)

    assert result.failure_class is None
    assert len(lifecycles) == 2 and lifecycles[1].requests == [request]

    base_session_id = _session_id_for(base)
    session_id = _session_id_for(request)
    # Each attempt is its own Step Execution and therefore its own canonical
    # session; the remediation turn is still journaled under its own source.
    assert session_id != base_session_id
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        assert await repos.sessions.get(session_id) is not None
    turns, commands, cleanup = await _turn_journal(session_factory, session_id)
    assert [turn.lineage_kind for turn in turns] == [TurnSource.REMEDIATION.value]
    assert len(commands) == 1
    assert commands[0].turn_attempt_id == turns[0].turn_attempt_id
    # An admitted turn fences incompatible cleanup before provider mutation.
    assert cleanup is not None and cleanup.generation >= 1
    base_turns, _, _ = await _turn_journal(session_factory, base_session_id)
    assert [turn.lineage_kind for turn in base_turns] == [TurnSource.INITIAL.value]


@pytest.mark.asyncio
async def test_non_remediation_dispatch_still_journals_initial(
    turn_commands, session_factory, plan
) -> None:
    """The derivation is authority-driven, not a blanket rename."""

    realizer, _ = _realizer(turn_commands, session_factory)
    request = _request(
        plan=plan, correlation_id="initial-run", idempotency_key="initial-key"
    )

    await realizer.execute(request, plan)

    turns, _, _ = await _turn_journal(session_factory, _session_id("initial-run"))
    assert [turn.lineage_kind for turn in turns] == [TurnSource.INITIAL.value]


@pytest.mark.asyncio
async def test_remediation_broadening_is_bounded_by_the_repaired_attempt(
    turn_commands, session_factory, plan
) -> None:
    """AC6 fires on a real dispatch, against the base attempt's own record.

    The remediation attempt bootstraps its *own* canonical session, so the only
    non-vacuous bound is the durable authority of the Step Execution it repairs.
    """

    realizer, lifecycles = _realizer(turn_commands, session_factory)
    base = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _base_node(publish_mode="none"),
        plan_ref=plan.planRef,
    )
    await realizer.execute(base, plan)
    assert len(lifecycles) == 1

    broadened = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _remediation_node(ordinal=1, publish_mode="branch"),
        plan_ref=plan.planRef,
    )
    with pytest.raises(RemediationAuthorityBroadenedError) as excinfo:
        await realizer.execute(broadened, plan)

    assert "publishMode" in excinfo.value.broadened
    # Refused before any provider mutation: no second lifecycle ran and no
    # command was ever journaled, so nothing owned delivery for this attempt.
    assert len(lifecycles) == 1
    _, broadened_commands, _ = await _turn_journal(
        session_factory, _session_id_for(broadened)
    )
    assert broadened_commands == []
    # The attempt being repaired keeps exactly its own turn and command.
    base_turns, base_commands, _ = await _turn_journal(
        session_factory, _session_id_for(base)
    )
    assert len(base_turns) == 1
    assert len(base_commands) == 1


@pytest.mark.asyncio
async def test_broadening_check_reads_the_base_record_not_the_same_claim(
    turn_commands, session_factory, plan
) -> None:
    """The identical broadened attempt passes when no base record exists.

    This is the control for the test above: if the guard compared the claim
    against the copy it had just written to its own session metadata it would
    behave identically in both cases, and AC6 would be vacuous in production.
    """

    realizer, lifecycles = _realizer(turn_commands, session_factory)
    broadened = _dispatch_request(
        _run_workflow(base_step_execution_id=BASE_STEP_EXECUTION_ID),
        _remediation_node(ordinal=1, publish_mode="branch"),
        plan_ref=plan.planRef,
    )

    # The repaired Step Execution never established a canonical session, so
    # there is no recorded authority the attempt could broaden.
    await realizer.execute(broadened, plan)

    assert len(lifecycles) == 1
    turns, _, _ = await _turn_journal(session_factory, _session_id_for(broadened))
    assert [turn.lineage_kind for turn in turns] == [TurnSource.REMEDIATION.value]


@pytest.mark.asyncio
async def test_realizer_cannot_reach_the_lifecycle_after_cleanup_completes(
    turn_commands, session_factory, plan
) -> None:
    """No production realizer call reaches the provider without a claim."""

    realizer, lifecycles = _realizer(turn_commands, session_factory)
    admitted = _request(
        plan=plan, correlation_id="cleaned-run", idempotency_key="admitted-key"
    )
    await realizer.execute(admitted, plan)
    assert len(lifecycles) == 1

    session_id = _session_id("cleaned-run")
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        janitor = await repos.cleanup.claim_cleanup(
            session_id, owner_class="janitor", claim_token="janitor-1"
        )
        done = await repos.cleanup.complete_cleanup(
            session_id,
            generation=janitor.record.generation,
            owner_class="janitor",
            claim_token="janitor-1",
            session_repository=repos.sessions,
        )
        assert done.applied is True

    follow_up = _request(
        plan=plan,
        correlation_id="cleaned-run",
        idempotency_key="follow-up-key",
    )
    with pytest.raises(HarnessPlatformError):
        await realizer.execute(follow_up, plan)
    assert len(lifecycles) == 1

    async with store.transaction() as repos:
        cleanup = await repos.cleanup.get(session_id)
    assert cleanup.state == CLEANUP_STATE_COMPLETE


# --- Every production realizer routes through the one wrapper ----------------


#: Every trusted execution realizer, keyed by the ref the registry admits. A new
#: realizer added to ``KNOWN_REALIZERS`` without an entry here fails the first
#: test below rather than silently escaping the routing assertion.
_PRODUCTION_REALIZERS = {
    CodexProfileBoundRealizer.ref: CodexProfileBoundRealizer,
    GenericOmnigentHostRealizer.ref: GenericOmnigentHostRealizer,
}


def test_every_trusted_realizer_is_covered_by_the_routing_assertion() -> None:
    assert set(_PRODUCTION_REALIZERS) == set(KNOWN_REALIZERS)


@pytest.mark.parametrize("realizer_class", sorted(
    _PRODUCTION_REALIZERS.values(), key=lambda cls: cls.ref
))
def test_no_production_realizer_names_its_own_turn_source(realizer_class) -> None:
    """The wrapper derives the source; a realizer may not assert one."""

    tree = ast.parse(textwrap.dedent(inspect.getsource(realizer_class.execute)))
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]  # the docstring may legitimately name the vocabulary
    code = "\n".join(ast.unparse(node) for node in body)
    assert "deliver_canonical_turn(" in code
    assert "turn_source" not in code
    assert "TurnSource." not in code
