"""Unit tests for the closed, versioned turn-source vocabulary.

Source: MoonLadderStudios/MoonMind#3707.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.control_plane.turn_sources import (
    TURN_SOURCE_KINDS,
    TURN_SOURCE_SCHEMA,
    TURN_SOURCE_VERSION,
    is_valid_turn_source,
    normalize_turn_source,
    turn_source_for_command_type,
    validate_turn_source,
)
from moonmind.omnigent.control_plane.turn_commands import (
    CanonicalSessionBootstrap,
    CanonicalTurnCommandService,
)
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
import pytest_asyncio
from api_service.db.models import Base


@pytest_asyncio.fixture()
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/turn_sources.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture()
async def store(session_factory):
    return OmnigentControlPlaneStore(session_factory)


def test_vocabulary_covers_required_sources() -> None:
    required = {
        "initial",
        "repository_continuation",
        "remediation",
        "workflow_chat",
        "steering",
        "approval_response",
        "checkpoint_resume",
        "linked_branch",
    }
    assert required.issubset(TURN_SOURCE_KINDS)
    assert TURN_SOURCE_SCHEMA == "moonmind.omnigent-turn-source.v1"
    assert TURN_SOURCE_VERSION == "v1"


def test_validate_accepts_closed_kinds() -> None:
    for kind in TURN_SOURCE_KINDS:
        assert validate_turn_source(kind) == kind
        assert is_valid_turn_source(kind) is True


def test_validate_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unknown turn source"):
        validate_turn_source("bad_kind")
    with pytest.raises(ValueError, match="unknown turn source"):
        validate_turn_source("continuation.bad")


def test_normalize_aliases() -> None:
    assert normalize_turn_source("continuation") == "repository_continuation"
    assert normalize_turn_source("approval") == "approval_response"
    assert normalize_turn_source("branch") == "linked_branch"
    assert normalize_turn_source("checkpoint") == "checkpoint_resume"


def test_turn_source_for_command_type_heuristic() -> None:
    assert turn_source_for_command_type("remediation_attempt") == "remediation"
    assert turn_source_for_command_type("workflow_chat_message") == "workflow_chat"
    assert turn_source_for_command_type("message") == "workflow_chat"
    assert turn_source_for_command_type("steering_interrupt") == "steering"
    assert turn_source_for_command_type("approval_response") == "approval_response"
    assert turn_source_for_command_type("checkpoint_resume") == "checkpoint_resume"
    assert turn_source_for_command_type("linked_branch") == "linked_branch"
    # generic fallback is repository_continuation, never an open string
    assert turn_source_for_command_type("execute_admitted_plan") == "repository_continuation"
    assert turn_source_for_command_type("unknown_command") == "repository_continuation"
    assert turn_source_for_command_type("") == "repository_continuation"


@pytest.mark.asyncio
async def test_claim_uses_closed_vocabulary_and_explicit_source(store) -> None:
    service = CanonicalTurnCommandService(store)

    # Explicit source is validated strictly
    claim = await service.claim(
        workflow_id="wf-explicit",
        provider_session_ref="",
        chat_binding_id=None,
        command_type="anything",
        idempotency_key="idem-explicit-1",
        payload_digest="sha256:" + "a" * 64,
        step_execution_id="step-explicit",
        bootstrap=CanonicalSessionBootstrap(
            provider="omnigent",
            step_execution_id="step-explicit",
            agent_run_id="agent-explicit",
            source_idempotency_key="idem-explicit-1",
        ),
        turn_source="remediation",
    )
    async with store.transaction() as repos:
        turn = await repos.turn_attempts.get(claim.turn_attempt_id)
    assert turn is not None and turn.lineage_kind == "remediation"

    # Invalid explicit source fails closed before mutation
    with pytest.raises(ValueError, match="unknown turn source"):
        await service.claim(
            workflow_id="wf-explicit",
            provider_session_ref="",
            chat_binding_id=None,
            command_type="anything",
            idempotency_key="idem-explicit-2",
            payload_digest="sha256:" + "b" * 64,
            step_execution_id="step-explicit",
            turn_source="bad_kind",
        )


@pytest.mark.asyncio
async def test_same_session_and_chat_preserved_across_turn_sources(store) -> None:
    session, initial = await store.establish_session(
        session_id="s-multi-source",
        moonmind_workflow_id="wf-multi-source",
        provider="omnigent",
        chat_binding_id="cb-multi-source",
        provider_session_ref="psess-multi-source",
        first_turn_attempt_id="t-initial",
        first_turn_idempotency_key="idem-initial",
    )
    service = CanonicalTurnCommandService(store)
    sources = [
        "repository_continuation",
        "remediation",
        "workflow_chat",
        "steering",
        "approval_response",
        "checkpoint_resume",
        "linked_branch",
    ]
    for idx, source in enumerate(sources):
        claim = await service.claim(
            workflow_id="wf-multi-source",
            provider_session_ref="psess-multi-source",
            chat_binding_id="cb-multi-source",
            command_type=f"cmd-{source}",
            idempotency_key=f"idem-{source}-{idx}",
            payload_digest=f"sha256:{idx:02d}" + "c" * 60,
            step_execution_id="step-multi",
            turn_source=source,
        )
        assert claim.session_id == session.session_id

    async with store.transaction() as repos:
        turns = await repos.turn_attempts.list_for_session(session.session_id)
        refreshed = await repos.sessions.get(session.session_id)
        alias = await repos.chat_binding_aliases.resolve("cb-multi-source")
    # initial + 7 follow-ups
    assert len(turns) == 8
    assert refreshed is not None and refreshed.chat_binding_id == "cb-multi-source"
    assert alias is not None and alias.session_id == session.session_id
    # every turn carries a closed vocabulary kind
    for t in turns:
        assert t.lineage_kind in TURN_SOURCE_KINDS
