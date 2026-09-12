"""Run the escaped admission/cleanup journey with real PostgreSQL authority."""

import pytest

from api_service.db.models import OmnigentExecutionPlanRecord
from tests.unit.omnigent.test_capacity_readmission_turn import (
    replay_capacity_readmission,
)
from tests.unit.omnigent.test_generic_platform_production_services import _plan

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]


async def test_capacity_readmission_turn_postgres(pg_store, monkeypatch):
    plan = _plan("opencode-go/model")
    async with pg_store._session_factory() as session:
        session.add(
            OmnigentExecutionPlanRecord(
                plan_ref=plan.planRef,
                schema_version=plan.schemaVersion,
                payload_json=plan.payload.model_dump(mode="json"),
                harness_id=plan.payload.harnessId,
                harness_implementation_ref=plan.payload.harnessImplementationRef,
                host_class_ref=plan.payload.hostClassRef,
                launch_policy_ref=plan.payload.launchPolicyRef,
                execution_realizer_ref=plan.payload.executionRealizerRef,
            )
        )
        await session.commit()
    await replay_capacity_readmission(pg_store, monkeypatch)
