"""Current plan writer -> durable DB/artifacts -> retained admission reader."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db import base
from api_service.db.models import Base
from api_service.services import omnigent_execution_plan_service as service
from moonmind.omnigent.harness_platform import execution_plan, stores
from moonmind.workflows.temporal.activities import (
    omnigent_session_activities as admission,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _compile_opencode_plan,
    _protected_support_evidence,
    _ready_opencode_image_pair,  # noqa: F401 -- isolated deployment observation
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.fixture
def retained_reader():
    path = (
        Path(__file__).with_name("replays")
        / "omnigent-plan-reader-skew/retained_execution_plan.py"
    )
    spec = importlib.util.spec_from_file_location("retained_plan_reader", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.mark.parametrize("reader", ["retained", "current"])
async def test_new_plan_reaches_registered_admission_without_writer_reader_sha_match(
    tmp_path,
    monkeypatch,
    retained_reader,
    reader,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plans.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        artifacts = _ArtifactService()
        monkeypatch.setattr(
            service,
            "resolve_execution_evidence",
            lambda plan, **_: (
                _protected_support_evidence(plan),
                "supported",
            ),
        )
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="omnigent-on-demand@1",
            plan_store=stores.DbExecutionPlanStore(maker),
        )
        # Artifact bytes and DB payload must both omit the redundant v1 field.
        serialized = json.loads(artifacts.payloads[compiled.binding.plan_artifact_ref])
        monkeypatch.setattr(base, "async_session_maker", maker)
        if reader == "retained":
            # Execute the actual reader source from the failing fleet, not a
            # permissive approximation of its Pydantic contract.
            monkeypatch.setattr(
                stores,
                "OmnigentExecutionPlanEnvelope",
                retained_reader.OmnigentExecutionPlanEnvelope,
            )
            monkeypatch.setattr(
                execution_plan,
                "verify_execution_plan_envelope",
                retained_reader.verify_execution_plan_envelope,
            )

        async def read_artifact(ref):
            return json.loads(artifacts.payloads[ref])

        monkeypatch.setattr(admission, "_read_json_artifact", read_artifact)
        monkeypatch.setattr(
            admission, "_plan_capacity_authority", AsyncMock(return_value={})
        )
        monkeypatch.setattr(
            "moonmind.omnigent.realizers.registry.get_default_registry",
            lambda: SimpleNamespace(require=lambda _: None),
        )
        # Execute the production worker method and full immutable admission
        # validation. Provider capacity/host creation are beyond this boundary.
        activities = object.__new__(TemporalAgentRuntimeActivities)
        result = await activities.omnigent_evaluate_session_admission(
            {
                "agentRunId": "plan-reader-replay",
                "workflowId": "mm:plan-reader-replay",
                "stepExecutionId": "implement",
                "executionProfileRef": "provider-opencode-native",
                "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
            }
        )
        assert result["reasonCode"] == "realizer_managed_lifecycle"
        assert "omnigentVersion" not in serialized["payload"]
        loaded = await stores.DbExecutionPlanStore(maker).load(
            compiled.envelope.planRef
        )
        assert loaded.planRef == compiled.envelope.planRef
        assert (
            loaded.payload.modelConfig.model_dump()
            == compiled.envelope.payload.modelConfig.model_dump()
        )
        serialized["payload"]["model"]["effort"] = "changed"
        artifacts.payloads[compiled.binding.plan_artifact_ref] = json.dumps(
            serialized
        ).encode()
        with pytest.raises(ValueError, match="digest mismatch|supportIdentity"):
            await admission._load_verified_execution_plan(compiled.binding)
    finally:
        await engine.dispose()
