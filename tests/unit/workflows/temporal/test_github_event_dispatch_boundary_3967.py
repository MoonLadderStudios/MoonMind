"""Real Temporal-server dispatch proof for the opt-in GitHub event path (#3967).

The router ingress suite proves ingress -> receipt -> admission against a
controllable fake dispatcher, and
``tests/unit/api/test_github_event_dispatch_3967.py`` proves the production
``TemporalEventExecutionDispatcher`` binding through the real
``TemporalExecutionService.create_execution`` admission gates against a real
database — but stubs the Temporal server ``start_workflow`` RPC with the
repo-standard ``mock_client_adapter`` fixture. The issue explicitly states a
fake submit callback alone is not full dispatch proof and names the real
Temporal/database boundary as the proof vehicle.

This module closes that named gap: the production dispatcher runs through
the real admission gates with a real ``TemporalClientAdapter`` bound to a
time-skipping ``WorkflowEnvironment`` test server, so the ``start_workflow``
hop is a real server RPC (not a stub). It asserts:

- the dispatch returns a real canonical ``mm:`` ref,
- the workflow actually exists on the Temporal server under that ref with
  the server-authoritative run identity reconciled into the canonical row,
- the canonical DB row carries the stable delivery identity key,
- a redelivery under the same identity key reconciles to the same logical
  execution (one canonical row, same ref).

The tree-wide ``prevent_live_temporal_lifecycle_calls`` autouse guard stubs
``TemporalClientAdapter.start_workflow``; this module restores the real
method for its own tests (captured at import, before any fixture runs) so
the hermetic test-server hop executes for real. The injected client is the
test ``WorkflowEnvironment`` client, never a live deployment connection.

Every test in this module is owned by the temporal-boundary shard via
tests/conftest.py; do not add ``pytest.mark.unit_fast`` here, it conflicts
with that ownership.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from temporalio.testing import WorkflowEnvironment

from api_service.db.models import Base, TemporalExecutionCanonicalRecord
from api_service.services import github_event_dispatch as dispatch_module
from api_service.services.github_event_dispatch import (
    TemporalEventExecutionDispatcher,
    build_dispatch_parameters,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from tests.helpers.temporal_visibility import register_deployment_search_attributes

# Captured at import: the tree-wide autouse lifecycle guard replaces this
# attribute during test setup, so the original is unreachable afterwards.
_REAL_ADAPTER_START_WORKFLOW = TemporalClientAdapter.start_workflow

_INSTALLATION = "12345"
_REPO = "acme/repo"


@pytest.mark.temporal_boundary
@pytest.mark.asyncio
async def test_production_dispatcher_starts_real_workflow_under_identity_key(
    tmp_path, monkeypatch
):
    """Production dispatcher -> real admission -> real Temporal server start."""
    # Restore the real server hop for this test only; the guard's monkeypatch
    # still undoes everything afterwards.
    monkeypatch.setattr(
        TemporalClientAdapter, "start_workflow", _REAL_ADAPTER_START_WORKFLOW
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dispatch-boundary.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await register_deployment_search_attributes(env)

            real_service_cls = dispatch_module.TemporalExecutionService

            def _service_factory(session: AsyncSession):
                return real_service_cls(
                    session, client_adapter=TemporalClientAdapter(env.client)
                )

            monkeypatch.setattr(
                dispatch_module, "TemporalExecutionService", _service_factory
            )

            delivery_id = f"del-boundary-{uuid4().hex[:12]}"
            identity_key = f"github-event:v1:{_INSTALLATION}:{_REPO}:{delivery_id}"
            parameters = build_dispatch_parameters(
                preset_slug="triage-preset",
                repository=_REPO,
                issue_number=7,
                delivery_key=f"github-delivery:v1:{_INSTALLATION}:{_REPO}:{delivery_id}",
                identity_key=identity_key,
                execution_limits={},
            )

            async with maker() as session:
                dispatcher = TemporalEventExecutionDispatcher(session)
                ref = await dispatcher.dispatch(
                    preset_slug="triage-preset",
                    identity_key=identity_key,
                    repository=_REPO,
                    issue_number=7,
                    title=f"GitHub event issues.labeled {_REPO}#7 [triage-preset]",
                    parameters=parameters,
                )
            assert ref.startswith("mm:"), ref

            # The workflow really started on the Temporal server: describe
            # the server-side execution under the returned ref.
            handle = env.client.get_workflow_handle(ref)
            description = await handle.describe()
            assert description.id == ref
            assert description.run_id

            # The canonical row carries the stable delivery identity key and
            # the server-authoritative run identity.
            async with maker() as session:
                rows = list(
                    (
                        await session.execute(
                            select(TemporalExecutionCanonicalRecord).where(
                                TemporalExecutionCanonicalRecord.create_idempotency_key
                                == identity_key
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(rows) == 1
            assert rows[0].workflow_id == ref
            assert rows[0].run_id == description.run_id

            # A redelivery under the same identity key reconciles to the same
            # logical execution instead of minting a second workflow.
            async with maker() as session:
                dispatcher = TemporalEventExecutionDispatcher(session)
                repeat_ref = await dispatcher.dispatch(
                    preset_slug="triage-preset",
                    identity_key=identity_key,
                    repository=_REPO,
                    issue_number=7,
                    title=f"GitHub event issues.labeled {_REPO}#7 [triage-preset]",
                    parameters=parameters,
                )
            assert repeat_ref == ref
            async with maker() as session:
                count = (
                    await session.execute(
                        select(func.count())
                        .select_from(TemporalExecutionCanonicalRecord)
                        .where(
                            TemporalExecutionCanonicalRecord.create_idempotency_key
                            == identity_key
                        )
                    )
                ).scalar_one()
            assert count == 1
    finally:
        await engine.dispose()
