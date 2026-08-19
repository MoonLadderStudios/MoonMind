"""Shared port-contract coverage for the Omnigent control-plane repositories.

Source: MoonLadderStudios/MoonMind#3711 ([Omnigent control plane 10/11]).

Proves that the in-memory reference adapters and the production SQLAlchemy
repositories are interchangeable behind one narrow port. The same behavioural
contract (``tests/helpers/omnigent_port_contracts.py``) is run against both
adapter families here on SQLite; the decisive PostgreSQL run lives beside the
other production invariants in
``tests/integration/omnigent/test_control_plane_postgres.py``.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.adapters.persistence import (
    InMemoryDecisionRepository,
    InMemoryObservationRepository,
)
from moonmind.omnigent.control_plane import OmnigentControlPlaneStore
from moonmind.omnigent.control_plane.repositories import (
    CommandRepository,
    DecisionRepository,
    ObservationRepository,
    SessionRepository,
    TurnAttemptRepository,
)
from moonmind.omnigent.ports import (
    CommandRepositoryPort,
    DecisionRepositoryPort,
    ObservationRepositoryPort,
    SessionRepositoryPort,
    TurnRepositoryPort,
)
from tests.helpers.omnigent_port_contracts import (
    run_decision_repository_contract,
    run_observation_repository_contract,
)

@pytest_asyncio.fixture()
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/port_contracts.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentControlPlaneStore(maker)
    finally:
        await engine.dispose()


async def _provision_sessions(repos, *session_ids: str) -> None:
    for index, session_id in enumerate(session_ids):
        await repos.sessions.create(
            session_id=session_id,
            moonmind_workflow_id=f"wf-{session_id}",
            provider="codex",
            provider_session_ref=f"psess-{session_id}",
        )


def test_production_repositories_satisfy_ports() -> None:
    """The concrete SQLAlchemy repositories are the ports' production adapters."""

    assert isinstance(SessionRepository.__new__(SessionRepository), SessionRepositoryPort)
    assert isinstance(
        TurnAttemptRepository.__new__(TurnAttemptRepository), TurnRepositoryPort
    )
    assert isinstance(
        ObservationRepository.__new__(ObservationRepository), ObservationRepositoryPort
    )
    assert isinstance(CommandRepository.__new__(CommandRepository), CommandRepositoryPort)
    assert isinstance(
        DecisionRepository.__new__(DecisionRepository), DecisionRepositoryPort
    )


def test_in_memory_adapters_satisfy_ports() -> None:
    assert isinstance(InMemoryObservationRepository(), ObservationRepositoryPort)
    assert isinstance(InMemoryDecisionRepository(), DecisionRepositoryPort)


@pytest.mark.asyncio
async def test_observation_contract_in_memory() -> None:
    await run_observation_repository_contract(
        InMemoryObservationRepository(), session_a="sa", session_b="sb"
    )


@pytest.mark.asyncio
async def test_decision_contract_in_memory() -> None:
    await run_decision_repository_contract(
        InMemoryDecisionRepository(), session_a="sa", session_b="sb"
    )


@pytest.mark.asyncio
async def test_observation_contract_sqlalchemy(store) -> None:
    async with store.transaction() as repos:
        await _provision_sessions(repos, "sa", "sb")
        await run_observation_repository_contract(
            repos.observations, session_a="sa", session_b="sb"
        )


@pytest.mark.asyncio
async def test_decision_contract_sqlalchemy(store) -> None:
    async with store.transaction() as repos:
        await _provision_sessions(repos, "sa", "sb")
        await run_decision_repository_contract(
            repos.decisions, session_a="sa", session_b="sb"
        )
