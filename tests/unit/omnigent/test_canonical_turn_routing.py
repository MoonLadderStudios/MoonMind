"""Canonical turn routing for every follow-up instruction source.

Source: MoonLadderStudios/MoonMind#3707 ([Omnigent control plane 7/11]).

These tests exercise the one typed canonical turn-command boundary that every
production producer -- repository continuation, remediation, Workflow Chat,
steering, approval response, checkpoint resume, linked branch -- must use:

* same-session and chat-binding preservation across zero, one, and seven
  continuations
* delivery ambiguity, stale fencing, and duplicate-request idempotency
* changed immutable execution authority returning an explicit typed decision
* cleanup racing an admitted turn, and historical reads surviving cleanup
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.control_plane import (
    CLEANUP_STATE_COMPLETE,
    ControlPlaneOutcome,
    FencingScope,
    OmnigentControlPlaneStore,
    TERMINAL_MEANINGS,
    TerminalMeaning,
    TurnSource,
    build_timeline,
    distinct_terminal_meanings,
    terminal_meaning_patch,
)
from moonmind.omnigent.control_plane.cleanup_authority import (
    CanonicalCleanupAuthority,
)
from moonmind.omnigent.control_plane.turn_admission import (
    IMMUTABLE_AUTHORITY_METADATA_KEY,
    IMMUTABLE_TURN_AUTHORITY_DIMENSIONS,
    CanonicalTurnAdmissionRejected,
    ImmutableTurnAuthority,
    RemediationAuthorityBroadenedError,
    assert_remediation_does_not_broaden,
    evaluate_turn_admission,
)
from moonmind.omnigent.control_plane.turn_commands import (
    CanonicalSessionBootstrap,
    CanonicalTurnAuthorityUnavailable,
    CanonicalTurnCommandService,
)
from moonmind.omnigent.control_plane.turn_sources import (
    TURN_SOURCES,
    TURN_SOURCE_VOCABULARY_VERSION,
    UnknownTurnSourceError,
    coerce_turn_source,
)
from moonmind.omnigent.harness_platform.execution_plan import ModelConfig
from moonmind.omnigent.realizers.generic_host import _GENERIC_HOST_CLEANUP_OWNER
from moonmind.omnigent.resume_decision import SessionResumeDecision
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _SESSION_SUPERVISOR_CLEANUP_OWNER,
)


WORKFLOW_ID = "wf-3707"
CHAT_BINDING = "chat-3707"
AGENT_RUN_ID = "agent-3707"
STEP_EXECUTION_ID = "step-3707"


#: Control-plane tables cleared between tests. Creating the full MoonMind schema
#: is the dominant cost of this suite, so the engine is built once per module and
#: only the aggregates under test are truncated per case.
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
    root = tmp_path_factory.mktemp("turn_routing")
    engine = create_async_engine(f"sqlite+aiosqlite:///{root}/turn_routing.db")
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
async def service(session_factory):
    return CanonicalTurnCommandService(OmnigentControlPlaneStore(session_factory))


def _bootstrap(**overrides) -> CanonicalSessionBootstrap:
    payload = {
        "provider": "omnigent",
        "step_execution_id": STEP_EXECUTION_ID,
        "agent_run_id": AGENT_RUN_ID,
        "source_idempotency_key": "run-1",
        "execution_plan_ref": "plan:sha256:" + "a" * 64,
    }
    payload.update(overrides)
    return CanonicalSessionBootstrap(**payload)


def _authority(**overrides) -> ImmutableTurnAuthority:
    dimensions = {name: f"{name}-value" for name in IMMUTABLE_TURN_AUTHORITY_DIMENSIONS}
    dimensions.update(overrides.pop("dimensions", {}))
    return ImmutableTurnAuthority(
        execution_plan_ref=overrides.pop(
            "execution_plan_ref", "plan:sha256:" + "a" * 64
        ),
        runtime_binding_ref=overrides.pop("runtime_binding_ref", "binding-1"),
        dimensions=dimensions,
    )


async def _claim(
    service: CanonicalTurnCommandService,
    *,
    idempotency_key: str,
    turn_source: TurnSource,
    command_type: str = "submit_instruction",
    chat_binding_id: str | None = CHAT_BINDING,
    payload_digest: str = "sha256:" + "d" * 64,
    **kwargs,
):
    return await service.claim(
        workflow_id=WORKFLOW_ID,
        provider_session_ref="",
        chat_binding_id=chat_binding_id,
        command_type=command_type,
        turn_source=turn_source,
        idempotency_key=idempotency_key,
        payload_digest=payload_digest,
        step_execution_id=STEP_EXECUTION_ID,
        bootstrap=kwargs.pop("bootstrap", _bootstrap()),
        **kwargs,
    )


# --- Closed, versioned source vocabulary ------------------------------------


def test_turn_source_vocabulary_is_closed_and_versioned() -> None:
    assert TURN_SOURCE_VOCABULARY_VERSION == 1
    assert TURN_SOURCES == {
        "initial",
        "repository_continuation",
        "remediation",
        "workflow_chat",
        "steering",
        "approval_response",
        "checkpoint_resume",
        "linked_branch",
    }
    assert coerce_turn_source("workflow_chat") is TurnSource.WORKFLOW_CHAT
    for retired in ("instruction", "continuation", "approval", ""):
        with pytest.raises(UnknownTurnSourceError):
            coerce_turn_source(retired)


# --- Same-session and chat tests --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("continuations", [0, 1, 7])
async def test_continuations_preserve_one_session_and_one_chat_binding(
    service, session_factory, continuations: int
) -> None:
    """Zero, one, and seven continuations keep one session and one binding."""

    initial = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    assert initial.outcome is ControlPlaneOutcome.APPLIED
    await service.settle(
        workflow_id=WORKFLOW_ID,
        idempotency_key="run-1",
        outcome=ControlPlaneOutcome.APPLIED,
    )

    turn_ids = {initial.turn_attempt_id}
    for index in range(1, continuations + 1):
        key = f"run-1:repository-continuation:{index}"
        claim = await _claim(
            service,
            idempotency_key=key,
            turn_source=TurnSource.REPOSITORY_CONTINUATION,
            command_type="repository_output_continuation",
        )
        assert claim.outcome is ControlPlaneOutcome.APPLIED
        assert claim.session_id == initial.session_id
        turn_ids.add(claim.turn_attempt_id)
        await service.settle(
            workflow_id=WORKFLOW_ID,
            idempotency_key=key,
            outcome=ControlPlaneOutcome.APPLIED,
        )

    # One canonical session, one chat binding, distinct turn identity each time.
    assert len(turn_ids) == continuations + 1
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(initial.session_id)
        assert session is not None
        assert session.chat_binding_id == CHAT_BINDING
        alias = await repos.chat_binding_aliases.resolve(CHAT_BINDING)
        assert alias is not None and alias.session_id == initial.session_id
        turns = await repos.turn_attempts.list_for_session(initial.session_id)
        assert len(turns) == continuations + 1
        assert turns[0].lineage_kind == TurnSource.INITIAL.value
        assert all(
            turn.lineage_kind == TurnSource.REPOSITORY_CONTINUATION.value
            for turn in turns[1:]
        )


@pytest.mark.asyncio
async def test_chat_continuation_and_remediation_share_one_command_journal(
    service, session_factory
) -> None:
    """Three different instruction sources land in one turn/command journal."""

    await _claim(service, idempotency_key="run-1", turn_source=TurnSource.INITIAL)
    sources = {
        "run-1:chat:1": TurnSource.WORKFLOW_CHAT,
        "run-1:repository-continuation:1": TurnSource.REPOSITORY_CONTINUATION,
        "run-1:remediation:1": TurnSource.REMEDIATION,
        "run-1:approval:1": TurnSource.APPROVAL_RESPONSE,
        "run-1:steering:1": TurnSource.STEERING,
        "run-1:linked-branch:1": TurnSource.LINKED_BRANCH,
    }
    session_ids = set()
    for key, source in sources.items():
        claim = await _claim(service, idempotency_key=key, turn_source=source)
        session_ids.add(claim.session_id)

    assert len(session_ids) == 1
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session_id = session_ids.pop()
        turns = await repos.turn_attempts.list_for_session(session_id)
        commands = await repos.commands.list_for_session(session_id)
        assert {turn.lineage_kind for turn in turns} == {
            TurnSource.INITIAL.value,
            *(source.value for source in sources.values()),
        }
        # One command journal owns every source's delivery.
        assert len(commands) == len(sources) + 1
        assert all(
            command.owner_class == CanonicalTurnCommandService.OWNER_CLASS
            for command in commands
        )


@pytest.mark.asyncio
async def test_terminal_turn_leaves_session_and_binding_interactive(
    service, session_factory
) -> None:
    """A completed turn does not terminalize the session or the binding."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        session = await repos.sessions.get(claim.session_id)
        await repos.turn_attempts.mark_terminal(
            claim.turn_attempt_id,
            terminal_state="completed",
            expected_revision=turn.revision,
            expected_fencing_generation=session.fencing_generation,
        )
    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        assert turn.is_terminal is True
        assert session.is_terminal is False
        assert session.historical_read_state == "live"

    # The same binding still admits a follow-up instruction.
    follow_up = await _claim(
        service, idempotency_key="run-1:chat:1", turn_source=TurnSource.WORKFLOW_CHAT
    )
    assert follow_up.session_id == claim.session_id
    assert follow_up.outcome is ControlPlaneOutcome.APPLIED


@pytest.mark.asyncio
async def test_final_session_terminality_refuses_further_same_session_work(
    service, session_factory
) -> None:
    """Final session terminality makes the same binding read-only."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL,
        requested_authority=_authority(),
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        await repos.sessions.mark_terminal(
            claim.session_id,
            terminal_state="completed",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )

    with pytest.raises(CanonicalTurnAdmissionRejected) as excinfo:
        await _claim(
            service,
            idempotency_key="run-1:chat:1",
            turn_source=TurnSource.WORKFLOW_CHAT,
            requested_authority=_authority(),
        )
    assert excinfo.value.decision in {
        SessionResumeDecision.BRANCH_REQUIRED,
        SessionResumeDecision.NEW_SESSION_REQUIRED,
    }
    assert "session_terminal" in excinfo.value.outcome.reason_codes


@pytest.mark.asyncio
async def test_cross_user_submission_fails_before_mutation(
    service, session_factory
) -> None:
    """Another principal cannot submit into this canonical session."""

    await _claim(
        service,
        idempotency_key="run-1",
        turn_source=TurnSource.INITIAL,
        bootstrap=_bootstrap(owner_principal="user-a"),
        actor_principal="user-a",
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        before = await repos.turn_attempts.count_for_session(
            (await repos.sessions.get_by_chat_binding(CHAT_BINDING)).session_id
        )

    with pytest.raises(CanonicalTurnAuthorityUnavailable):
        await _claim(
            service,
            idempotency_key="run-1:chat:1",
            turn_source=TurnSource.WORKFLOW_CHAT,
            bootstrap=_bootstrap(owner_principal="user-b"),
            actor_principal="user-b",
        )

    async with store.transaction() as repos:
        session = await repos.sessions.get_by_chat_binding(CHAT_BINDING)
        assert await repos.turn_attempts.count_for_session(session.session_id) == before


@pytest.mark.asyncio
async def test_cross_binding_submission_fails_before_mutation(service) -> None:
    """A binding bound to another workflow cannot reach this session."""

    await _claim(service, idempotency_key="run-1", turn_source=TurnSource.INITIAL)
    with pytest.raises(CanonicalTurnAuthorityUnavailable):
        await service.claim(
            workflow_id="wf-other",
            provider_session_ref="",
            chat_binding_id=CHAT_BINDING,
            command_type="submit_instruction",
            turn_source=TurnSource.WORKFLOW_CHAT,
            idempotency_key="run-1:chat:cross",
            payload_digest="sha256:" + "e" * 64,
            step_execution_id=STEP_EXECUTION_ID,
            bootstrap=None,
        )


# --- Delivery and retry tests ------------------------------------------------


@pytest.mark.asyncio
async def test_lost_provider_response_parks_delivery_ambiguity(
    service, session_factory
) -> None:
    """A provider accepted turn whose response is lost is not resubmitted."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    await service.settle(
        workflow_id=WORKFLOW_ID,
        idempotency_key="run-1",
        outcome=ControlPlaneOutcome.DELIVERY_UNKNOWN,
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        command = await repos.commands.get(claim.command_id)
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        assert command.delivery_ambiguous is True
        assert turn.state == "delivery_unknown"

    # The retry observes the ambiguity instead of blindly submitting again.
    retry = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    assert retry.outcome is not ControlPlaneOutcome.APPLIED
    assert retry.turn_attempt_id == claim.turn_attempt_id
    assert retry.command_id == claim.command_id


@pytest.mark.asyncio
async def test_duplicate_request_reuses_one_logical_turn_identity(service) -> None:
    """A duplicate browser or controller request is one logical turn."""

    first = await _claim(
        service,
        idempotency_key="run-1:chat:1",
        turn_source=TurnSource.WORKFLOW_CHAT,
    )
    second = await _claim(
        service,
        idempotency_key="run-1:chat:1",
        turn_source=TurnSource.WORKFLOW_CHAT,
    )
    # One logical turn, one command journal entry, one delivery claim token.
    assert first.turn_attempt_id == second.turn_attempt_id
    assert first.command_id == second.command_id
    assert first.idempotency_key == second.idempotency_key
    assert first.claim_token == second.claim_token
    assert first.outcome is ControlPlaneOutcome.APPLIED
    # The duplicate resumes the same durable claim rather than manufacturing a
    # second turn; it never becomes an independent delivery.
    assert second.outcome is ControlPlaneOutcome.APPLIED

    # Once the first delivery settles, the duplicate can no longer redeliver.
    await service.settle(
        workflow_id=WORKFLOW_ID,
        idempotency_key="run-1:chat:1",
        outcome=ControlPlaneOutcome.APPLIED,
    )
    third = await _claim(
        service,
        idempotency_key="run-1:chat:1",
        turn_source=TurnSource.WORKFLOW_CHAT,
    )
    assert third.outcome is ControlPlaneOutcome.ALREADY_APPLIED
    assert third.turn_attempt_id == first.turn_attempt_id


@pytest.mark.asyncio
async def test_stale_activity_result_is_fenced_by_newer_generation(
    service, session_factory
) -> None:
    """A settle carrying a superseded fencing generation cannot regress state."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        # A strictly newer supervisor generation takes ownership.
        refreshed = await repos.sessions.acquire_fencing_generation(
            claim.session_id,
            FencingScope.SESSION_SUPERVISOR,
            expected_revision=session.revision,
        )
        assert refreshed.fencing_generation > session.fencing_generation
        # The stale activity result presents the superseded generation.
        stale = await repos.turn_attempts.compare_and_swap_turn(
            claim.turn_attempt_id,
            expected_revision=turn.revision,
            expected_fencing_generation=session.fencing_generation,
            state="accepted",
        )
        assert stale.outcome is ControlPlaneOutcome.FENCING_CONFLICT
        unchanged = await repos.turn_attempts.get(claim.turn_attempt_id)
        assert unchanged.state == turn.state


# --- Immutable-authority / resume decision tests ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dimension",
    ["harnessId", "model", "repository", "workspaceIntentRef", "publishMode"],
)
async def test_changed_immutable_authority_requires_branch_before_mutation(
    service, session_factory, dimension: str
) -> None:
    """Changed model, harness, repository, workspace, or publication branches."""

    claim = await _claim(
        service,
        idempotency_key="run-1",
        turn_source=TurnSource.INITIAL,
        requested_authority=_authority(),
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        before = await repos.turn_attempts.count_for_session(claim.session_id)

    with pytest.raises(CanonicalTurnAdmissionRejected) as excinfo:
        await _claim(
            service,
            idempotency_key="run-1:chat:1",
            turn_source=TurnSource.WORKFLOW_CHAT,
            requested_authority=_authority(dimensions={dimension: "changed"}),
        )
    assert excinfo.value.decision is SessionResumeDecision.BRANCH_REQUIRED
    assert f"immutable_{dimension}_changed" in excinfo.value.outcome.reason_codes

    # Nothing was mutated on the prior session.
    async with store.transaction() as repos:
        assert await repos.turn_attempts.count_for_session(claim.session_id) == before


@pytest.mark.asyncio
async def test_unchanged_authority_admits_the_same_session(service) -> None:
    claim = await _claim(
        service,
        idempotency_key="run-1",
        turn_source=TurnSource.INITIAL,
        requested_authority=_authority(),
    )
    follow_up = await _claim(
        service,
        idempotency_key="run-1:chat:1",
        turn_source=TurnSource.WORKFLOW_CHAT,
        requested_authority=_authority(),
    )
    assert follow_up.session_id == claim.session_id
    assert follow_up.outcome is ControlPlaneOutcome.APPLIED


def test_resume_decision_contract_is_one_closed_vocabulary() -> None:
    assert {member.value for member in SessionResumeDecision} == {
        "live_reattach",
        "cold_restore",
        "branch_required",
        "new_session_required",
        "resume_unavailable",
    }


def test_missing_branch_evidence_escalates_to_new_session_required() -> None:
    outcome = evaluate_turn_admission(
        recorded=_authority(),
        requested=_authority(dimensions={"model": "changed"}),
        branch_capable=False,
    )
    assert outcome.decision is SessionResumeDecision.NEW_SESSION_REQUIRED
    assert outcome.requires_new_authority is True


def test_changed_runtime_binding_is_never_a_live_reattach() -> None:
    """The runtime binding is immutable authority, not an incidental pointer.

    Two bindings under the same execution plan carry different host, lease, and
    workspace authority, so reattaching across them would reuse a runtime the
    session never recorded.
    """

    outcome = evaluate_turn_admission(
        recorded=_authority(runtime_binding_ref="binding-1"),
        requested=_authority(runtime_binding_ref="binding-2"),
    )
    assert outcome.decision is SessionResumeDecision.BRANCH_REQUIRED
    assert "immutable_runtimeBindingRef_changed" in outcome.reason_codes


def test_an_unasserted_runtime_binding_keeps_the_recorded_authority() -> None:
    outcome = evaluate_turn_admission(
        recorded=_authority(runtime_binding_ref="binding-1"),
        requested=_authority(runtime_binding_ref=None),
    )
    assert outcome.decision is SessionResumeDecision.LIVE_REATTACH


def test_execution_plan_projection_reads_the_qualified_model_id() -> None:
    """``ModelConfig`` names the selected model ``qualifiedId``.

    Projecting ``model`` recorded ``None`` on both sides of the guard, so a
    billing-relevant model change could never be detected.
    """

    plan = SimpleNamespace(
        planRef="plan:sha256:" + "b" * 64,
        payload=SimpleNamespace(
            harnessId="codex",
            executionRealizerRef="codex-profile-bound@1",
            modelConfig=ModelConfig(
                qualifiedId="anthropic/claude-opus-5",
                effort="high",
                modelConfigDigest="sha256:" + "c" * 64,
            ),
            workspaceIntentRef="workspace-1",
            resolvedSkills="skills-1",
            launchPolicyRef="launch-1",
        ),
    )

    authority = ImmutableTurnAuthority.from_execution_plan(plan)

    assert authority.dimensions["model"] == "anthropic/claude-opus-5"
    assert authority.dimensions["effort"] == "high"


def test_live_reattach_requires_current_runtime_authority() -> None:
    live = evaluate_turn_admission(recorded=_authority(), requested=_authority())
    assert live.decision is SessionResumeDecision.LIVE_REATTACH
    cold = evaluate_turn_admission(
        recorded=_authority(),
        requested=_authority(),
        runtime_authority_current=False,
    )
    assert cold.decision is SessionResumeDecision.COLD_RESTORE
    assert cold.same_session is True


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", IMMUTABLE_TURN_AUTHORITY_DIMENSIONS)
async def test_remediation_cannot_broaden_bounded_authority(
    service, session_factory, dimension: str
) -> None:
    """Remediation may not widen any immutable execution authority dimension."""

    claim = await _claim(
        service,
        idempotency_key="run-1",
        turn_source=TurnSource.INITIAL,
        requested_authority=_authority(),
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        before = await repos.turn_attempts.count_for_session(claim.session_id)

    with pytest.raises(RemediationAuthorityBroadenedError) as excinfo:
        await _claim(
            service,
            idempotency_key="run-1:remediation:1",
            turn_source=TurnSource.REMEDIATION,
            requested_authority=_authority(dimensions={dimension: "broadened"}),
        )
    assert dimension in excinfo.value.broadened

    async with store.transaction() as repos:
        assert await repos.turn_attempts.count_for_session(claim.session_id) == before


@pytest.mark.asyncio
async def test_remediation_within_the_same_authority_is_admitted(service) -> None:
    claim = await _claim(
        service,
        idempotency_key="run-1",
        turn_source=TurnSource.INITIAL,
        requested_authority=_authority(),
    )
    remediation = await _claim(
        service,
        idempotency_key="run-1:remediation:1",
        turn_source=TurnSource.REMEDIATION,
        requested_authority=_authority(),
    )
    assert remediation.session_id == claim.session_id
    assert remediation.outcome is ControlPlaneOutcome.APPLIED


#: A remediation attempt runs as its own Step Execution, so it bootstraps its
#: own canonical session and cannot be bounded by that session's own metadata.
ATTEMPT_STEP_EXECUTION_ID = "step-3707:remediation:1"


async def _bootstrapping_remediation_claim(
    service: CanonicalTurnCommandService,
    *,
    base_step_execution_id: str | None,
    requested_authority,
):
    """Claim a remediation turn that establishes its own canonical session."""

    return await service.claim(
        workflow_id=WORKFLOW_ID,
        provider_session_ref="",
        chat_binding_id=None,
        command_type="submit_instruction",
        turn_source=TurnSource.REMEDIATION,
        idempotency_key="run-1:remediation:1",
        payload_digest="sha256:" + "d" * 64,
        step_execution_id=ATTEMPT_STEP_EXECUTION_ID,
        base_step_execution_id=base_step_execution_id,
        bootstrap=_bootstrap(
            step_execution_id=ATTEMPT_STEP_EXECUTION_ID,
            source_idempotency_key="run-1:remediation:1",
        ),
        requested_authority=requested_authority,
    )


@pytest.mark.asyncio
async def test_bootstrapping_remediation_is_bounded_by_the_named_base(
    service,
) -> None:
    """AC6 compares against the repaired Step Execution's durable record."""

    await _claim(
        service,
        idempotency_key="run-1",
        turn_source=TurnSource.INITIAL,
        chat_binding_id=None,
        requested_authority=_authority(),
    )

    with pytest.raises(RemediationAuthorityBroadenedError) as excinfo:
        await _bootstrapping_remediation_claim(
            service,
            base_step_execution_id=STEP_EXECUTION_ID,
            requested_authority=_authority(dimensions={"publishMode": "broadened"}),
        )

    assert "publishMode" in excinfo.value.broadened


@pytest.mark.asyncio
async def test_a_bootstrapping_remediation_is_never_its_own_bound(service) -> None:
    """The claim's own bootstrap copy can never satisfy the AC6 comparison.

    Naming the claiming Step Execution as its own base would make the guard
    compare the request with the metadata it just wrote, which is exactly the
    vacuous check this boundary must not perform.
    """

    claim = await _bootstrapping_remediation_claim(
        service,
        base_step_execution_id=ATTEMPT_STEP_EXECUTION_ID,
        requested_authority=_authority(dimensions={"publishMode": "broadened"}),
    )

    assert claim.outcome is ControlPlaneOutcome.APPLIED


@pytest.mark.asyncio
async def test_remediation_without_a_recorded_base_has_nothing_to_broaden(
    service,
) -> None:
    """A repaired Step Execution that never opened a session records no bound."""

    claim = await _bootstrapping_remediation_claim(
        service,
        base_step_execution_id="step-3707:never-canonicalized",
        requested_authority=_authority(dimensions={"publishMode": "broadened"}),
    )

    assert claim.outcome is ControlPlaneOutcome.APPLIED


@pytest.mark.asyncio
async def test_a_terminal_session_is_refused_even_without_asserted_authority(
    service, session_factory
) -> None:
    """Bridge chat, steering, approval, and branch callers assert no authority.

    Skipping the terminal decision for them let the claim create a turn and hit
    ``TerminalSessionOverwriteError`` -- an unhandled bridge error -- instead of
    the documented typed branch outcome.
    """

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        await repos.sessions.mark_terminal(
            claim.session_id,
            terminal_state="completed",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )
        before = await repos.turn_attempts.count_for_session(claim.session_id)

    with pytest.raises(CanonicalTurnAdmissionRejected) as excinfo:
        await _claim(
            service,
            idempotency_key="run-1:chat:1",
            turn_source=TurnSource.WORKFLOW_CHAT,
            requested_authority=None,
        )

    assert excinfo.value.decision is SessionResumeDecision.BRANCH_REQUIRED
    assert "session_terminal" in excinfo.value.outcome.reason_codes
    async with store.transaction() as repos:
        assert await repos.turn_attempts.count_for_session(claim.session_id) == before


@pytest.mark.asyncio
async def test_the_first_asserted_authority_is_persisted_for_legacy_sessions(
    service, session_factory
) -> None:
    """A converged legacy session must record the authority it converged onto.

    Migration 366 does not backfill ``immutableTurnAuthority``, so without a
    durable write every later instruction re-enters the same convergence branch
    and a changed model, profile, repository, Skill, or publication authority is
    silently accepted instead of requiring a branch.
    """

    legacy = await _claim(
        service,
        idempotency_key="run-1",
        turn_source=TurnSource.INITIAL,
        requested_authority=None,
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(legacy.session_id)
    assert IMMUTABLE_AUTHORITY_METADATA_KEY not in session.metadata

    await _claim(
        service,
        idempotency_key="run-1:chat:1",
        turn_source=TurnSource.WORKFLOW_CHAT,
        requested_authority=_authority(),
    )

    async with store.transaction() as repos:
        session = await repos.sessions.get(legacy.session_id)
    assert (
        session.metadata[IMMUTABLE_AUTHORITY_METADATA_KEY]
        == _authority().as_metadata()
    )

    with pytest.raises(CanonicalTurnAdmissionRejected) as excinfo:
        await _claim(
            service,
            idempotency_key="run-1:chat:2",
            turn_source=TurnSource.WORKFLOW_CHAT,
            requested_authority=_authority(dimensions={"model": "changed"}),
        )
    assert "immutable_model_changed" in excinfo.value.outcome.reason_codes


def test_remediation_is_bounded_by_every_immutable_dimension() -> None:
    """AC6 bounds remediation by the whole immutable set, not a subset.

    A subset silently allowed a remediation to raise effort, adopt a rotated
    Provider Profile generation, or retarget another repository or branch.
    """

    recorded = _authority()
    for dimension in IMMUTABLE_TURN_AUTHORITY_DIMENSIONS:
        with pytest.raises(RemediationAuthorityBroadenedError) as excinfo:
            assert_remediation_does_not_broaden(
                recorded=recorded,
                requested=_authority(dimensions={dimension: "broadened"}),
            )
        assert excinfo.value.broadened == (dimension,)


def test_incomplete_asserted_authority_fails_closed_when_required() -> None:
    outcome = evaluate_turn_admission(
        recorded=_authority(),
        requested=_authority(dimensions={"model": None}),
        require_complete_authority=True,
    )
    assert outcome.decision is SessionResumeDecision.RESUME_UNAVAILABLE
    assert "immutable_model_missing" in outcome.reason_codes


# --- Cleanup coordination tests ----------------------------------------------


@pytest.mark.asyncio
async def test_every_production_cleanup_owner_shares_this_aggregate(
    service, session_factory
) -> None:
    """The teardown owners production actually runs claim *this* row.

    ``fence_for_turn`` only fences a real janitor when the owners that release
    hosts, credentials, and provider sessions hold this claim. Both production
    owners -- the legacy session supervisor and the generic host realizer --
    resolve their claim through :class:`CanonicalCleanupAuthority`, so a second
    owner is refused while the first holds it.
    """

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    authority = CanonicalCleanupAuthority(OmnigentControlPlaneStore(session_factory))

    supervisor = await authority.claim(
        claim.session_id, owner_class=_SESSION_SUPERVISOR_CLEANUP_OWNER
    )
    assert supervisor is not None
    # The same owner's next destructive step resumes its own claim.
    assert (
        await authority.claim(
            claim.session_id, owner_class=_SESSION_SUPERVISOR_CLEANUP_OWNER
        )
    ).generation == supervisor.generation
    # A second production owner is refused while that claim is live.
    assert (
        await authority.claim(
            claim.session_id, owner_class=_GENERIC_HOST_CLEANUP_OWNER
        )
        is None
    )
    assert await authority.complete(supervisor) is True

    async with OmnigentControlPlaneStore(session_factory).transaction() as repos:
        cleanup = await repos.cleanup.get(claim.session_id)
    assert cleanup.state == CLEANUP_STATE_COMPLETE


@pytest.mark.asyncio
async def test_an_admitted_turn_fences_the_shared_cleanup_owner(
    service, session_factory
) -> None:
    """A turn admitted after the claim stops that owner completing cleanup.

    This is the whole point of advancing the cleanup generation: the janitor
    that already released nothing irreversible cannot report the replacement
    generation as cleaned.
    """

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    authority = CanonicalCleanupAuthority(OmnigentControlPlaneStore(session_factory))
    janitor = await authority.claim(
        claim.session_id, owner_class=_GENERIC_HOST_CLEANUP_OWNER
    )
    assert janitor is not None

    await _claim(
        service,
        idempotency_key="run-1:continuation:1",
        turn_source=TurnSource.REPOSITORY_CONTINUATION,
    )

    assert await authority.complete(janitor) is False
    async with OmnigentControlPlaneStore(session_factory).transaction() as repos:
        cleanup = await repos.cleanup.get(claim.session_id)
    assert cleanup.state != CLEANUP_STATE_COMPLETE


@pytest.mark.asyncio
async def test_recovery_resolves_its_session_through_the_attached_provider(
    service, session_factory
) -> None:
    """A recovery scan knows the provider session, not the workflow scope."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    await service.attach_provider_session(
        session_id=claim.session_id,
        provider_session_ref="provider-session-77",
        fencing_generation=claim.fencing_generation,
    )
    authority = CanonicalCleanupAuthority(OmnigentControlPlaneStore(session_factory))

    assert await authority.resolve_session_id("provider-session-77") == (
        claim.session_id
    )
    assert await authority.resolve_session_id("provider-session-unknown") == ""


@pytest.mark.asyncio
async def test_cleanup_racing_a_new_turn_is_fenced_deterministically(
    service, session_factory
) -> None:
    """An admitted turn fences an outstanding janitor claim."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        janitor = await repos.cleanup.claim_cleanup(
            claim.session_id, owner_class="janitor", claim_token="janitor-1"
        )
        assert janitor.applied is True
        claimed_generation = janitor.record.generation

    # A continuation is admitted while the janitor holds its claim.
    await _claim(
        service,
        idempotency_key="run-1:repository-continuation:1",
        turn_source=TurnSource.REPOSITORY_CONTINUATION,
    )

    async with store.transaction() as repos:
        completion = await repos.cleanup.complete_cleanup(
            claim.session_id,
            generation=claimed_generation,
            owner_class="janitor",
            claim_token="janitor-1",
            session_repository=repos.sessions,
        )
    # The former janitor cannot clean the replacement generation.
    assert completion.outcome is ControlPlaneOutcome.NOT_OWNER
    async with store.transaction() as repos:
        current = await repos.cleanup.get(claim.session_id)
        assert current.generation > claimed_generation
        assert current.state != CLEANUP_STATE_COMPLETE


@pytest.mark.asyncio
async def test_turn_cannot_consume_credentials_after_cleanup_completes(
    service, session_factory
) -> None:
    """Completed cleanup refuses a same-session turn instead of reopening it."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        janitor = await repos.cleanup.claim_cleanup(
            claim.session_id, owner_class="janitor", claim_token="janitor-1"
        )
        done = await repos.cleanup.complete_cleanup(
            claim.session_id,
            generation=janitor.record.generation,
            owner_class="janitor",
            claim_token="janitor-1",
            session_repository=repos.sessions,
        )
        assert done.applied is True

    with pytest.raises(CanonicalTurnAdmissionRejected) as excinfo:
        await _claim(
            service,
            idempotency_key="run-1:repository-continuation:1",
            turn_source=TurnSource.REPOSITORY_CONTINUATION,
            requested_authority=_authority(),
        )
    assert "cleanup_complete" in excinfo.value.outcome.reason_codes


# --- Distinct terminality + historical reads ---------------------------------


def test_terminal_meanings_stay_distinct() -> None:
    assert TERMINAL_MEANINGS == {
        "turn_attempt",
        "provider_session",
        "agent_run",
        "step_execution",
        "workflow",
        "remediation_controller",
        "branch",
        "cleanup",
    }


@pytest.mark.asyncio
async def test_every_terminal_plane_is_projected_independently(
    service, session_factory
) -> None:
    """Turn, session, workflow, remediation, branch, and cleanup stay separate."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        await repos.turn_attempts.mark_terminal(
            claim.turn_attempt_id,
            terminal_state="completed",
            expected_revision=turn.revision,
            expected_fencing_generation=session.fencing_generation,
        )
        patch: dict = {}
        patch.update(
            terminal_meaning_patch(TerminalMeaning.WORKFLOW, state="completed")
        )
        patch.update(terminal_meaning_patch(TerminalMeaning.BRANCH, state="failed"))
        patch.update(
            terminal_meaning_patch(
                TerminalMeaning.REMEDIATION_CONTROLLER, state="exhausted"
            )
        )
        await repos.sessions.bind_runtime_authority(
            claim.session_id,
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
            metadata_patch=patch,
        )

    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
        cleanup = await repos.cleanup.get(claim.session_id)
        meanings = distinct_terminal_meanings(
            session=session, active_turn=turn, cleanup=cleanup
        )

    assert meanings["turn_attempt"] == "completed"
    assert meanings["workflow"] == "completed"
    assert meanings["branch"] == "failed"
    assert meanings["remediation_controller"] == "exhausted"
    # A completed turn and a terminal Workflow do not terminalize the session,
    # and no cleanup has completed.
    assert meanings["provider_session"] is None
    assert meanings["cleanup"] is None


@pytest.mark.asyncio
async def test_history_remains_readable_after_full_cleanup(
    service, session_factory
) -> None:
    """Transcript, timeline, and evidence survive provider/host/credential cleanup."""

    claim = await _claim(
        service, idempotency_key="run-1", turn_source=TurnSource.INITIAL
    )
    await service.settle(
        workflow_id=WORKFLOW_ID,
        idempotency_key="run-1",
        outcome=ControlPlaneOutcome.APPLIED,
        result_ref="artifact://terminal-evidence",
    )
    store = OmnigentControlPlaneStore(session_factory)
    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        await repos.sessions.mark_terminal(
            claim.session_id,
            terminal_state="completed",
            terminal_evidence_ref="artifact://terminal-evidence",
            expected_revision=session.revision,
            expected_fencing_generation=session.fencing_generation,
        )
        janitor = await repos.cleanup.claim_cleanup(
            claim.session_id, owner_class="janitor", claim_token="janitor-1"
        )
        await repos.cleanup.complete_cleanup(
            claim.session_id,
            generation=janitor.record.generation,
            owner_class="janitor",
            claim_token="janitor-1",
            session_repository=repos.sessions,
        )

    async with store.transaction() as repos:
        session = await repos.sessions.get(claim.session_id)
        turns = await repos.turn_attempts.list_for_session(claim.session_id)
        commands = await repos.commands.list_for_session(claim.session_id)
        cleanup = await repos.cleanup.get(claim.session_id)
        timeline = build_timeline(
            session=session,
            turn_attempts=turns,
            observations=[],
            commands=commands,
            decisions=[],
            cleanup=cleanup,
        )

    assert session.historical_read_state == "live"
    assert timeline.terminal_evidence_ref == "artifact://terminal-evidence"
    assert timeline.terminal_meanings["cleanup"] == CLEANUP_STATE_COMPLETE
    assert timeline.terminal_meanings["provider_session"] == "completed"
    assert timeline.turn_attempt_count == len(turns) == 1
    assert timeline.to_dict()["terminal"]["meanings"]["cleanup"] == (
        CLEANUP_STATE_COMPLETE
    )
