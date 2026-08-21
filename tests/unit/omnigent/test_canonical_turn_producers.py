"""Production-boundary coverage for every canonical turn producer.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane] route all
continuations, remediation, checkpoints, and chat through canonical sessions and
turn attempts).

``tests/unit/omnigent/test_canonical_turn_boundary.py`` proves the boundary
itself. This module proves the *callers*: that every key in
``TURN_PRODUCER_SOURCES`` names a live production call site, that the server-owned
Checkpoint Branch turn launcher and the profile-bound coordinator submit through
that one boundary before any mutation, and that the production service is built
with the supervisor dispatcher. The seams exercised here are the real production
functions -- ``CheckpointBranchTurnExecutionOwner._start_claimed_turn`` and
``OmnigentProfileBoundExecutionCoordinator._submit_canonical_turn`` -- against a
real control-plane store, not test-only stand-ins.
"""

from __future__ import annotations

import json
import pathlib
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    CheckpointBranchTurnLaunchError,
)
from moonmind.omnigent.control_plane import (
    IMMUTABLE_AUTHORITY_METADATA_KEY,
    SUPERVISOR_GENERATION_METADATA_KEY,
    OmnigentControlPlaneStore,
)
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.omnigent.turn_contracts import (
    RUNTIME_AUTHORITY_INCOMPLETE,
    SESSION_TERMINAL,
    TURN_PRODUCER_SOURCES,
    ImmutableExecutionAuthority,
    OmnigentTurnSource,
    RuntimeAuthorityEvidence,
    TurnDisposition,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

SOURCE_WORKFLOW_ID = "source-workflow"
PROVIDER_SESSION_REF = "provider-session-1"
RECORDED_AUTHORITY = ImmutableExecutionAuthority(
    executionPlanRef="artifact://intent/1",
    providerProfileId="profile-1",
    repositoryRef="repo:example",
    branchRef="refs/heads/main",
)


# --- Fixtures ----------------------------------------------------------------


@pytest_asyncio.fixture()
async def engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/producers.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture()
async def session_factory(engine):
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture()
async def store(session_factory):
    return OmnigentControlPlaneStore(session_factory)


class _RecordingSignalClient:
    """Stands in for the Temporal client the production dispatcher requires."""

    def __init__(self) -> None:
        self.signals: list[tuple[str, str, dict]] = []

    async def signal_workflow(self, workflow_id, signal_name, arg=None):
        self.signals.append((workflow_id, signal_name, dict(arg or {})))


@pytest.fixture()
def signal_client(monkeypatch: pytest.MonkeyPatch) -> _RecordingSignalClient:
    """Bind the production dispatcher to a recording signal client."""

    client = _RecordingSignalClient()
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter",
        lambda *args, **kwargs: client,
    )
    return client


async def _establish(
    store,
    *,
    session_id: str = "oms_producer",
    supervised: bool = True,
    provider_session_ref: str = PROVIDER_SESSION_REF,
    authority: ImmutableExecutionAuthority | None = None,
):
    metadata = {
        "actorId": "user-1",
        IMMUTABLE_AUTHORITY_METADATA_KEY: (
            authority or RECORDED_AUTHORITY
        ).as_dict(),
    }
    if supervised:
        metadata[SUPERVISOR_GENERATION_METADATA_KEY] = "omnigent-session-v1"
    session, _turn = await store.establish_session(
        session_id=session_id,
        moonmind_workflow_id=SOURCE_WORKFLOW_ID,
        provider="omnigent",
        provider_session_ref=provider_session_ref,
        chat_binding_id=f"cb-{session_id}",
        first_turn_attempt_id=f"{session_id}-t0",
        first_turn_idempotency_key=f"{session_id}-idem-0",
        step_execution_id="step-1",
        metadata=metadata,
    )
    return session


# --- The inventory may not claim coverage it does not have --------------------


def test_every_registered_producer_has_a_production_call_site() -> None:
    """AC1 guard: the inventory can never drift back into aspiration.

    A producer key that exists only in the inventory and its tests is an
    alternate submission path hiding behind a registration: the boundary looks
    covered while the real caller still submits somewhere else. Every key must
    therefore be referenced from production (non-test) code other than the
    inventory that declares it.
    """

    searched = [
        path
        for directory in ("moonmind", "api_service")
        for path in (REPO_ROOT / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    inventory = REPO_ROOT / "moonmind" / "omnigent" / "turn_contracts.py"
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in searched
        if path != inventory
    }

    missing: dict[str, str] = {}
    for producer in TURN_PRODUCER_SOURCES:
        needle = f'"{producer}"'
        callers = sorted(
            str(path.relative_to(REPO_ROOT))
            for path, text in sources.items()
            if needle in text
        )
        if not callers:
            missing[producer] = "no production call site"
    assert not missing, missing


def test_production_producers_are_the_only_submitters() -> None:
    """No production module may construct the boundary outside the one factory.

    ``production_turn_service`` is the single seam that binds the supervisor
    dispatcher, so a direct ``CanonicalTurnService(...)`` construction in
    production code would be a second delivery authority.
    """

    offenders: list[str] = []
    for directory in ("moonmind", "api_service"):
        for path in (REPO_ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"CanonicalTurnService\(", text) and path.name not in {
                "turn_service.py",
                "supervisor_turn_dispatch.py",
            }:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


# --- The production service is dispatcher-bound -------------------------------


LIVE_EVIDENCE = RuntimeAuthorityEvidence(
    providerSessionAttached=True,
    providerSessionResumable=True,
    hostAttached=True,
    hostLeaseActive=True,
    credentialLeaseActive=True,
    providerProfileGenerationCurrent=True,
    workspaceAvailable=True,
)


@pytest.mark.asyncio
async def test_production_turn_service_is_dispatcher_bound(
    store, signal_client
) -> None:
    """The one production factory wires the supervisor signal sender.

    Delivery ownership is the condition, not session state alone: a turn that
    declares supervisor delivery on a supervisor-owned session is signalled with
    the admitted source, while a producer-delivered turn is not -- signalling it
    too would submit the same turn to the provider twice. A pre-canonical
    session is never signalled into a workflow that does not exist.
    """

    from moonmind.omnigent.control_plane import CanonicalTurnRequest
    from moonmind.omnigent.supervisor_turn_dispatch import (
        SUBMIT_TURN_SIGNAL,
        SupervisorTurnDispatcher,
        production_turn_service,
    )

    service = production_turn_service(store)
    assert isinstance(service._dispatcher, SupervisorTurnDispatcher)

    supervised = await _establish(store, session_id="oms_supervised")
    legacy = await _establish(
        store,
        session_id="oms_legacy",
        supervised=False,
        provider_session_ref="provider-session-legacy",
    )

    def _request(
        session_id: str, key: str, *, supervisor_delivered: bool
    ) -> CanonicalTurnRequest:
        return CanonicalTurnRequest(
            producer="omnigent.repository_output_continuation",
            session_id=session_id,
            turn_attempt_id=f"turn-{key}",
            idempotency_key=key,
            instruction_ref=f"artifact://instructions/{key}",
            source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
            parent_turn_attempt_id=f"{session_id}-t0",
            evidence=LIVE_EVIDENCE,
            supervisor_delivered=supervisor_delivered,
        )

    dispatched = await service.submit_turn(
        _request(supervised.session_id, "k-1", supervisor_delivered=True)
    )
    assert dispatched.admitted is True
    assert (dispatched.supervised, dispatched.dispatched) == (True, True)
    workflow_id, signal_name, payload = signal_client.signals[-1]
    assert workflow_id == f"omnigent-session:{supervised.session_id}"
    assert signal_name == SUBMIT_TURN_SIGNAL
    assert payload["turnSource"] == "repository_continuation"

    producer_delivered = await service.submit_turn(
        _request(supervised.session_id, "k-2", supervisor_delivered=False)
    )
    assert producer_delivered.admitted is True
    assert (producer_delivered.supervised, producer_delivered.dispatched) == (
        True,
        False,
    )
    assert len(signal_client.signals) == 1

    unsupervised = await service.submit_turn(
        _request(legacy.session_id, "k-3", supervisor_delivered=True)
    )
    assert unsupervised.admitted is True
    assert (unsupervised.supervised, unsupervised.dispatched) == (False, False)
    assert len(signal_client.signals) == 1


# --- Checkpoint Branch turn launcher (AC1) ------------------------------------


def _branch_authority(*, remediation: bool = False):
    branch = SimpleNamespace(
        branch_id="branch-1",
        workflow_id=SOURCE_WORKFLOW_ID,
        parent_branch_id=None,
        parent_turn_id=None,
        logical_step_id="implement",
        diagnostics={},
    )
    turn = SimpleNamespace(
        branch_turn_id="turn-1",
        branch_id="branch-1",
        instruction_ref="artifact://branch/instructions",
        instruction_digest="sha256:" + "1" * 64,
        source_checkpoint_ref="artifact://branch/checkpoint",
        created_step_execution_id="checkpoint-branch-turn:turn-1",
        runtime_agent_run_id="agent-run-1",
        step_execution_manifest_ref="artifact://branch/manifest",
        diagnostics={
            "executionWorkflowId": "checkpoint-branch-turn:turn-1",
            **(
                {"remediationContextRef": "artifact://remediation/context"}
                if remediation
                else {}
            ),
        },
    )
    binding = SimpleNamespace(
        repository="repo:example",
        base_branch="refs/heads/main",
        base_commit="c" * 40,
        work_branch="mm/branch-1",
    )
    return branch, turn, binding


def _branch_manifest() -> bytes:
    return json.dumps(
        {
            "agentExecutionRequest": {
                "agentKind": "external",
                "agentId": "omnigent",
                "executionProfileRef": "profile-1",
                "correlationId": "turn-1",
                "idempotencyKey": "branch-launch-1",
                "instructionRef": "artifact://branch/instructions",
                "workspaceSpec": {
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": "ws-1",
                        "relativePath": "repo",
                    }
                },
                "parameters": {"publishMode": "pr"},
                "checkpointRecovery": {
                    "recoveryAction": "branch_required",
                    "omnigentCheckpoint": {
                        "workflowId": SOURCE_WORKFLOW_ID,
                        "omnigentSessionId": PROVIDER_SESSION_REF,
                    },
                },
            }
        }
    ).encode()


def _branch_owner(engine, *, remediation: bool = False):
    started: list[dict] = []

    class _Client:
        async def start_workflow(self, **kwargs):
            started.append(kwargs)

    session = SimpleNamespace(
        bind=engine, get=AsyncMock(return_value=SimpleNamespace(namespace="default", run_id="run-1"))
    )
    owner = CheckpointBranchTurnExecutionOwner(
        session,  # type: ignore[arg-type]
        principal="service:test",
        client=_Client(),  # type: ignore[arg-type]
        artifact_service=SimpleNamespace(),  # type: ignore[arg-type]
    )
    owner._read_ref = AsyncMock(return_value=_branch_manifest())  # type: ignore[method-assign]
    branch, turn, binding = _branch_authority(remediation=remediation)
    return owner, branch, turn, binding, started


async def _decisions(store, session_id: str):
    async with store.transaction() as repos:
        return await repos.decisions.list_for_session(session_id)


@pytest.mark.asyncio
async def test_branch_turn_launcher_submits_linked_branch_before_start(
    engine, store, signal_client
) -> None:
    """AC1: the server-owned launcher is no longer an independent authority.

    The turn reaches the canonical boundary as ``omnigent.checkpoint_branch_turn``
    / ``linked_branch``, is told ``new_session_required`` (a branch never reuses
    the source session), and only then starts its durable owner. The refusal is
    journalled on the source session as durable evidence.
    """

    session = await _establish(store, session_id="oms_branch")
    owner, branch, turn, binding, started = _branch_owner(engine)

    await owner._start_claimed_turn(branch=branch, turn=turn, binding=binding)

    assert len(started) == 1
    journal = await _decisions(store, session.session_id)
    refusals = [
        record
        for record in journal
        if record.product_visible_transition
        == TurnDisposition.NEW_SESSION_REQUIRED.value
    ]
    assert refusals, [r.product_visible_transition for r in journal]
    # A branch decision never mutates the source session's active turn.
    async with store.transaction() as repos:
        refreshed = await repos.sessions.get(session.session_id)
        turns = await repos.turn_attempts.list_for_session(session.session_id)
    assert refreshed.active_turn_attempt_id == f"{session.session_id}-t0"
    assert [item.turn_source for item in turns] == ["initial"]
    # A refused submission is never delivered as new work.
    assert signal_client.signals == []


@pytest.mark.asyncio
async def test_branch_turn_launcher_submits_remediation_for_owned_context(
    engine, store, signal_client
) -> None:
    """AC6: a remediation branch is refused for broadening locked authority.

    The producer is chosen from the durable remediation context the turn carries,
    not from a caller label, and the remediation-locked ``branchRef`` dimension
    makes the decision ``branch_required`` -- the launcher's authorization to
    allocate new canonical authority instead of mutating the prior session.
    """

    session = await _establish(store, session_id="oms_remediation")
    owner, branch, turn, binding, started = _branch_owner(engine, remediation=True)

    await owner._start_claimed_turn(branch=branch, turn=turn, binding=binding)

    assert len(started) == 1
    journal = await _decisions(store, session.session_id)
    branch_required = [
        record
        for record in journal
        if record.product_visible_transition
        == TurnDisposition.BRANCH_REQUIRED.value
    ]
    assert branch_required
    assert branch_required[-1].reason_code == "remediation_would_broaden_authority"


@pytest.mark.asyncio
async def test_branch_turn_launcher_fails_closed_without_checkpoint_evidence(
    engine, store, signal_client
) -> None:
    """``resume_unavailable`` never starts the durable owner.

    A linked branch must name the artifact-backed checkpoint it branches from.
    Without it the decision is ``resume_unavailable`` -- no safe path -- which is
    not permission to allocate new authority, so the owner is never started and
    the operator sees the canonical disposition rather than a launcher-invented
    failure.
    """

    session = await _establish(store, session_id="oms_fenced")
    owner, branch, turn, binding, started = _branch_owner(engine)
    turn.source_checkpoint_ref = ""

    with pytest.raises(CheckpointBranchTurnLaunchError) as excinfo:
        await owner._start_claimed_turn(branch=branch, turn=turn, binding=binding)
    assert excinfo.value.code == "canonical_turn_not_admitted"
    assert "resume_unavailable" in excinfo.value.reason
    assert started == []
    journal = await _decisions(store, session.session_id)
    assert journal[-1].reason_code == "checkpoint_evidence_required"


@pytest.mark.asyncio
async def test_branch_turn_launcher_leaves_pre_canonical_source_untouched(
    engine, store
) -> None:
    """A source with no canonical session row stays on the existing path."""

    owner, branch, turn, binding, started = _branch_owner(engine)
    await owner._start_claimed_turn(branch=branch, turn=turn, binding=binding)
    assert len(started) == 1


# --- Profile-bound coordinator producers (AC1) --------------------------------


def _coordinator(session_factory) -> OmnigentProfileBoundExecutionCoordinator:
    return OmnigentProfileBoundExecutionCoordinator(
        session_factory=session_factory,
        lease_client=SimpleNamespace(),  # type: ignore[arg-type]
        host_repository=SimpleNamespace(),  # type: ignore[arg-type]
        host_runtime=SimpleNamespace(),  # type: ignore[arg-type]
        run_store=SimpleNamespace(),  # type: ignore[arg-type]
        execution_runner=AsyncMock(),
        artifact_gateway=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_coordinator_continuation_and_resume_share_one_boundary(
    session_factory, store, signal_client
) -> None:
    """The continuation loop and checkpoint resume are one canonical authority.

    Both producers resolve the same canonical session by its recorded scope and
    journal onto the same turn repository and command journal, so a
    repository-output continuation and a checkpoint resume can never become two
    submission authorities for one session.
    """

    session = await _establish(store, session_id="oms_coordinator")
    coordinator = _coordinator(session_factory)

    live = RuntimeAuthorityEvidence(
        providerSessionAttached=True,
        providerSessionResumable=True,
        hostAttached=True,
        hostLeaseActive=True,
        credentialLeaseActive=True,
        providerProfileGenerationCurrent=True,
        workspaceAvailable=True,
    )
    continuation = await coordinator._submit_canonical_turn(
        producer="omnigent.repository_output_continuation",
        source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
        moonmind_workflow_id=SOURCE_WORKFLOW_ID,
        provider_session_ref=PROVIDER_SESSION_REF,
        instruction_ref=(
            f"omnigent-continuation://{PROVIDER_SESSION_REF}/"
            "repository-publication/1"
        ),
        idempotency_key="run-1:repository-continuation:1",
        evidence=live,
    )
    assert continuation is not None
    continuation.require_same_session()
    assert continuation.disposition is TurnDisposition.LIVE_REATTACH

    resume = await coordinator._submit_canonical_turn(
        producer="omnigent.checkpoint_resume",
        source=OmnigentTurnSource.CHECKPOINT_RESUME,
        moonmind_workflow_id=SOURCE_WORKFLOW_ID,
        provider_session_ref=PROVIDER_SESSION_REF,
        instruction_ref="artifact://checkpoint/external-state",
        idempotency_key="run-1:resume",
        checkpoint_ref="artifact://checkpoint/workspace",
        evidence=RuntimeAuthorityEvidence(checkpointRestorable=True),
    )
    assert resume is not None
    resume.require_same_session()
    assert resume.disposition is TurnDisposition.COLD_RESTORE

    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(session.session_id)
        commands = await repos.commands.list_for_session(session.session_id)
    assert sorted(item.turn_source for item in turns) == [
        "checkpoint_resume",
        "initial",
        "repository_continuation",
    ]
    submitted = [
        command
        for command in commands
        if command.command_type == "omnigent.submit_turn"
    ]
    assert len(submitted) == 2
    # Both producers forward their own turn, so neither is signalled to the
    # supervisor: signalling a producer-delivered turn would submit it twice.
    assert signal_client.signals == []
    assert (continuation.dispatched, resume.dispatched) == (False, False)


@pytest.mark.asyncio
async def test_coordinator_refuses_continuation_on_terminal_session(
    session_factory, store, signal_client
) -> None:
    """A terminal session refuses new work but keeps its historical reads."""

    session = await _establish(store, session_id="oms_terminal")
    async with store.transaction() as repos:
        await repos.sessions.mark_terminal(
            session.session_id,
            "completed",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )
    coordinator = _coordinator(session_factory)

    decision = await coordinator._submit_canonical_turn(
        producer="omnigent.repository_output_continuation",
        source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
        moonmind_workflow_id=SOURCE_WORKFLOW_ID,
        provider_session_ref=PROVIDER_SESSION_REF,
        instruction_ref="omnigent-continuation://x/repository-publication/1",
        idempotency_key="run-1:repository-continuation:1",
        evidence=RuntimeAuthorityEvidence(
            providerSessionAttached=True,
            providerSessionResumable=True,
            hostAttached=True,
            hostLeaseActive=True,
            credentialLeaseActive=True,
            providerProfileGenerationCurrent=True,
            workspaceAvailable=True,
        ),
    )
    assert decision is not None
    assert decision.admitted is False
    assert decision.decision.reason_code == SESSION_TERMINAL
    with pytest.raises(Exception) as excinfo:
        decision.require_same_session()
    assert "new_session_required" in str(excinfo.value)
    assert signal_client.signals == []


@pytest.mark.asyncio
async def test_coordinator_refuses_resume_without_runtime_or_restore_evidence(
    session_factory, store, signal_client
) -> None:
    """Neither live authority nor artifact-backed restore evidence: no path."""

    await _establish(store, session_id="oms_no_evidence")
    coordinator = _coordinator(session_factory)

    decision = await coordinator._submit_canonical_turn(
        producer="omnigent.checkpoint_resume",
        source=OmnigentTurnSource.CHECKPOINT_RESUME,
        moonmind_workflow_id=SOURCE_WORKFLOW_ID,
        provider_session_ref=PROVIDER_SESSION_REF,
        instruction_ref="artifact://checkpoint/external-state",
        idempotency_key="run-1:resume",
        checkpoint_ref="artifact://checkpoint/workspace",
        evidence=RuntimeAuthorityEvidence(),
    )
    assert decision is not None
    assert decision.decision.reason_code == RUNTIME_AUTHORITY_INCOMPLETE
    assert decision.disposition is TurnDisposition.RESUME_UNAVAILABLE
    assert signal_client.signals == []


@pytest.mark.asyncio
async def test_coordinator_skips_pre_canonical_scope(session_factory) -> None:
    """No canonical session row means no canonical authority to bind."""

    coordinator = _coordinator(session_factory)
    assert (
        await coordinator._submit_canonical_turn(
            producer="omnigent.repository_output_continuation",
            source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
            moonmind_workflow_id="unknown-workflow",
            provider_session_ref="unknown-session",
            instruction_ref="omnigent-continuation://x/repository-publication/1",
            idempotency_key="run-1:repository-continuation:1",
        )
        is None
    )


# --- The recorded plan a turn is admitted against (AC2) -----------------------


@pytest.mark.asyncio
async def test_resolve_intent_records_the_plan_turns_are_admitted_against(
    session_factory, store, signal_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: session establishment writes the immutable execution authority.

    Turn admission compares every later submission against the *recorded* plan.
    Without a production writer, every canonical submission is refused with
    ``execution_plan_not_recorded`` -- so this drives the real
    ``omnigent.resolve_intent`` Activity and then submits a real continuation
    through the canonical boundary to prove the recorded plan makes it admissible.
    """

    from moonmind.workflows.temporal.activities import (
        omnigent_session_activities as activities,
    )

    monkeypatch.setattr("api_service.db.base.async_session_maker", session_factory)

    async def _fake_artifact(*, name, artifact_type, payload):
        assert artifact_type == "omnigent.compiled_execution_intent"
        return "artifact://intent/resolved"

    monkeypatch.setattr(activities, "_write_json_artifact", _fake_artifact)

    resolved = await activities.omnigent_resolve_intent_activity(
        {
            "request": {
                "agentKind": "external",
                "agentId": "omnigent",
                "executionProfileRef": "profile-1",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-resolve-1",
                "instructionRef": "artifact://instructions/resolve",
                "parameters": {
                    "model": "model-a",
                    "effort": "high",
                    "repository": "repo:example",
                    "targetBranch": "refs/heads/work",
                    "publishMode": "pr",
                    "omnigent": {"target": {"launchPolicyRef": "launch-policy:1"}},
                },
            },
            "workflowId": "mm:w-resolve",
            "stepExecutionId": "step-resolve-1",
            "agentRunId": "agent-run-resolve-1",
        }
    )
    session_id = resolved["sessionId"]

    async with store.transaction() as repos:
        established = await repos.sessions.get(session_id)
    recorded = established.metadata[IMMUTABLE_AUTHORITY_METADATA_KEY]
    assert recorded["executionPlanRef"] == "artifact://intent/resolved"
    assert recorded["providerProfileId"] == "profile-1"
    assert recorded["repositoryRef"] == "repo:example"
    assert recorded["branchRef"] == "refs/heads/work"
    assert recorded["launchPolicyRef"] == "launch-policy:1"
    assert recorded["publicationAuthorityRef"] == "pr"
    # Session establishment is the only writer; no credential-shaped value is
    # ever recorded here.
    assert "credentialRef" not in recorded

    # The recorded plan is what makes a later same-session turn admissible.
    coordinator = _coordinator(session_factory)
    async with store.transaction() as repos:
        await repos.sessions.attach_provider_session(
            session_id,
            "provider-session-resolved",
            expected_revision=established.revision,
            expected_fencing_generation=established.fencing_generation,
        )
    decision = await coordinator._submit_canonical_turn(
        producer="omnigent.repository_output_continuation",
        source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
        moonmind_workflow_id="mm:w-resolve",
        provider_session_ref="provider-session-resolved",
        instruction_ref=(
            "omnigent-continuation://provider-session-resolved/"
            "repository-publication/1"
        ),
        idempotency_key="idem-resolve-1:repository-continuation:1",
        evidence=LIVE_EVIDENCE,
    )
    assert decision is not None
    decision.require_same_session()
    assert decision.disposition is TurnDisposition.LIVE_REATTACH
    assert decision.turn_attempt.execution_plan_ref == "artifact://intent/resolved"
    assert decision.turn_attempt.authority_digest is not None


@pytest.mark.asyncio
async def test_unrecorded_plan_refuses_same_session_reuse(
    session_factory, store, signal_client
) -> None:
    """A session with no recorded plan is told to allocate a new one.

    This is the failure the production writer above exists to prevent: unknown
    immutable authority is never silently reused.
    """

    await _establish(
        store,
        session_id="oms_no_plan",
        provider_session_ref="provider-session-no-plan",
        authority=ImmutableExecutionAuthority(),
    )
    coordinator = _coordinator(session_factory)
    decision = await coordinator._submit_canonical_turn(
        producer="omnigent.repository_output_continuation",
        source=OmnigentTurnSource.REPOSITORY_CONTINUATION,
        moonmind_workflow_id=SOURCE_WORKFLOW_ID,
        provider_session_ref="provider-session-no-plan",
        instruction_ref="omnigent-continuation://x/repository-publication/1",
        idempotency_key="no-plan:repository-continuation:1",
        evidence=LIVE_EVIDENCE,
    )
    assert decision is not None
    assert decision.decision.reason_code == "execution_plan_not_recorded"
    assert decision.disposition is TurnDisposition.NEW_SESSION_REQUIRED
