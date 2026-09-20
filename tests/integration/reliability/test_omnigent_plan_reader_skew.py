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


@pytest.fixture
def retained_preflight():
    path = (
        Path(__file__).with_name("replays")
        / "omnigent-plan-reader-skew/retained_deployment_identity.py"
    )
    spec = importlib.util.spec_from_file_location("retained_deployment_identity", path)
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
    retained_preflight,
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
        if reader == "retained":
            # Parsing alone does not prove launchability. Exercise the exact
            # retained validator under the incident's unchanged deployment.
            # That binary predates patch interoperability; its narrower
            # admitted authority must reject replacement before any launch.
            planned = loaded.payload.supportIdentity.omnigentServerBuildRef
            monkeypatch.setattr(
                retained_preflight, "resolve_deployed_server_build_digest", lambda: planned
            )
            monkeypatch.setattr(
                retained_preflight, "_resolve_deployed_host_image_ref",
                lambda _: loaded.payload.hostImageRef,
            )
            retained_preflight.assert_plan_matches_deployed_runtime(loaded.payload)
            monkeypatch.setattr(
                retained_preflight, "resolve_deployed_server_build_digest",
                lambda: "sha256:" + "f" * 64,
            )
            with pytest.raises(retained_preflight.OmnigentDeploymentIdentityConflict):
                retained_preflight.assert_plan_matches_deployed_runtime(loaded.payload)
            assert loaded.planRef == compiled.envelope.planRef
        serialized["payload"]["model"]["effort"] = "changed"
        artifacts.payloads[compiled.binding.plan_artifact_ref] = json.dumps(
            serialized
        ).encode()
        with pytest.raises(ValueError, match="digest mismatch|supportIdentity"):
            await admission._load_verified_execution_plan(compiled.binding)
    finally:
        await engine.dispose()


async def test_stable_upstream_agent_source_survives_admission(tmp_path, monkeypatch):
    """Writer-pinned stable agent source must admit at launch.

    The plan compiler pins the Agent Profile document's own upstream
    projection digest so a model-only profile version bump does not change
    ``agentSourceRef``. The admission reader has to verify that same identity;
    comparing the profile *version* digest instead rejects every launch whose
    document carries a genuine upstream snapshot digest.
    """

    stable_projection = "sha256:" + "a" * 64
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plans.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        artifacts = _ArtifactService()
        monkeypatch.setattr(
            service,
            "resolve_execution_evidence",
            lambda plan, **_: (_protected_support_evidence(plan), "supported"),
        )
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="omnigent-on-demand@1",
            plan_store=stores.DbExecutionPlanStore(maker),
            document_source={
                "upstreamId": "opencode-native-agent",
                "upstreamVersion": "174",
                "upstreamSnapshotDigest": stable_projection,
            },
        )
        planned_source = compiled.envelope.payload.agentSource
        profile_ref = compiled.envelope.payload.agentProfileSnapshotRef.removeprefix(
            "artifact:"
        )
        profile_snapshot = json.loads(artifacts.payloads[profile_ref])
        # The incident's precondition: the stable source digest and the
        # profile version digest are different values.
        assert planned_source["upstreamSnapshotDigest"] == stable_projection
        assert profile_snapshot["digest"] != stable_projection

        monkeypatch.setattr(base, "async_session_maker", maker)

        async def read_artifact(ref):
            return json.loads(artifacts.payloads[ref])

        monkeypatch.setattr(admission, "_read_json_artifact", read_artifact)
        loaded = await admission._load_verified_execution_plan(compiled.binding)
        assert loaded.payload.agentSource["upstreamSnapshotDigest"] == (
            stable_projection
        )

        # A genuinely different upstream projection must still be rejected.
        conflicting = json.loads(artifacts.payloads[profile_ref])
        conflicting["document"]["source"]["upstreamSnapshotDigest"] = (
            "sha256:" + "b" * 64
        )
        artifacts.payloads[profile_ref] = json.dumps(conflicting).encode()
        with pytest.raises(ValueError, match="conflicts with planned source identity"):
            await admission._load_verified_execution_plan(compiled.binding)
    finally:
        await engine.dispose()
