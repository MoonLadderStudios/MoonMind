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
import hashlib
import inspect
import textwrap
from datetime import UTC, datetime

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


def _remediation_workspace(*, workflow_id: str, step_execution_id: str) -> dict:
    """Return the controller-produced remediation authority for one attempt."""

    workspace_id = hashlib.sha256(
        f"{workflow_id}:{step_execution_id}".encode()
    ).hexdigest()[:24]
    return {
        "loopId": "loop-1",
        "branchRef": "checkpoint-branch:loop-1",
        "attemptOrdinal": 2,
        "workflowId": workflow_id,
        "runId": "temporal-run-1",
        "logicalStepId": "remediate",
        "stepExecutionId": step_execution_id,
        "baseCheckpointRef": "artifact://workspace/C1",
        "baseWorkspaceDigest": "sha256:" + "a" * 64,
        "expectedHeadVersion": 2,
        "headAuthorityRef": "artifact://loop-head/2",
        "destinationWorkspaceLocator": {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        },
        "executionProfileRef": PROVIDER_PROFILE_REF,
        "hostProfileRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "workspaceCapabilitySnapshot": {"locatorKind": "sandbox", "restore": True},
    }


def _request(
    *,
    plan,
    correlation_id: str,
    idempotency_key: str,
    publish_mode: str = "none",
    remediation: bool = False,
) -> AgentExecutionRequest:
    payload = {
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
    if remediation:
        payload["remediationWorkspace"] = _remediation_workspace(
            workflow_id=correlation_id, step_execution_id=correlation_id
        )
    return AgentExecutionRequest.model_validate(payload)


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
    """The canonical session identity the realizer boundary derives itself."""

    return canonical_omnigent_session_id(
        workflow_id=correlation_id,
        step_execution_id=correlation_id,
        agent_run_id=correlation_id,
    )


async def _turn_journal(session_factory, session_id: str):
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(session_id)
        commands = await repos.commands.list_for_session(session_id)
        cleanup = await repos.cleanup.get(session_id)
    return turns, commands, cleanup


# --- The typed request is the only source authority --------------------------


def test_turn_source_is_derived_from_typed_request_authority(plan) -> None:
    """Only controller-produced remediation authority names REMEDIATION."""

    initial = _request(plan=plan, correlation_id="run-a", idempotency_key="key-a")
    remediation = _request(
        plan=plan,
        correlation_id="run-a",
        idempotency_key="key-b",
        remediation=True,
    )
    assert canonical_turn_source(initial) is TurnSource.INITIAL
    assert canonical_turn_source(remediation) is TurnSource.REMEDIATION


# --- RW-1: the remediation producer --------------------------------------------


@pytest.mark.asyncio
async def test_remediation_dispatched_through_the_realizer_journals_remediation(
    turn_commands, session_factory, plan
) -> None:
    """A remediation attempt persists lineage_kind='remediation' (#3707 AC3)."""

    realizer, lifecycles = _realizer(turn_commands, session_factory)
    request = _request(
        plan=plan,
        correlation_id="remediation-run",
        idempotency_key="remediation-key",
        remediation=True,
    )

    result = await realizer.execute(request, plan)

    assert result.failure_class is None
    assert len(lifecycles) == 1 and lifecycles[0].requests == [request]

    session_id = _session_id("remediation-run")
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        assert await repos.sessions.get(session_id) is not None
    turns, commands, cleanup = await _turn_journal(session_factory, session_id)
    assert [turn.lineage_kind for turn in turns] == [TurnSource.REMEDIATION.value]
    assert len(commands) == 1
    assert commands[0].turn_attempt_id == turns[0].turn_attempt_id
    # An admitted turn fences incompatible cleanup before provider mutation.
    assert cleanup is not None and cleanup.generation >= 1


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
async def test_remediation_broadening_is_refused_at_the_realizer_boundary(
    turn_commands, session_factory, plan
) -> None:
    """AC6 fires on a real dispatch, before the Codex lifecycle runs."""

    realizer, lifecycles = _realizer(turn_commands, session_factory)
    admitted = _request(
        plan=plan,
        correlation_id="bounded-run",
        idempotency_key="admitted-key",
        publish_mode="none",
    )
    await realizer.execute(admitted, plan)
    assert len(lifecycles) == 1

    broadened = _request(
        plan=plan,
        correlation_id="bounded-run",
        idempotency_key="remediation-key",
        publish_mode="branch",
        remediation=True,
    )
    with pytest.raises(RemediationAuthorityBroadenedError) as excinfo:
        await realizer.execute(broadened, plan)

    assert "publishMode" in excinfo.value.broadened
    # Refused before any provider mutation: no second lifecycle, no second turn.
    assert len(lifecycles) == 1
    turns, commands, _ = await _turn_journal(
        session_factory, _session_id("bounded-run")
    )
    assert len(turns) == 1
    assert len(commands) == 1


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
        idempotency_key="remediation-key",
        remediation=True,
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
