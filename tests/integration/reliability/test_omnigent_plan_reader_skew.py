"""Current plan writer -> durable DB/artifacts -> retained admission reader."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db import base
from api_service.db.models import Base, TemporalArtifact
from api_service.services import omnigent_execution_plan_service as service
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import link_verified_execution_plan_inputs
from moonmind.omnigent.harness_platform import execution_plan, stores
from moonmind.workflows.temporal.activities import (
    omnigent_session_activities as admission,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    LocalTemporalArtifactStore,
)
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401 -- isolated deployment observation
    _compile_opencode_plan,
    _protected_support_evidence,
    _ready_opencode_image_pair,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


WORKFLOW = "mm:plan-reader-replay"


@pytest.fixture(autouse=True)
def local_artifact_storage(tmp_path, monkeypatch):
    # Runtime catalog probing is outside this retained plan reader boundary.
    monkeypatch.setattr(
        service, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )


async def _admit_inputs(maker, compiled):
    await link_verified_execution_plan_inputs(
        session_factory=maker,
        plan=compiled.envelope,
        binding=compiled.binding,
        workflow_id=WORKFLOW,
        run_id="reader-run",
        access=ArtifactAccessContext.for_service("user-1"),
    )


async def _read_artifact(artifacts, ref):
    _, payload = await artifacts.read(
        artifact_id=ref.removeprefix("artifact:"),
        access=ArtifactAccessContext.for_execution(WORKFLOW),
    )
    return json.loads(payload)


async def _task_input(artifacts):
    return await service.persist_json_artifact(
        artifact_service=artifacts,
        principal="user-1",
        artifact_class="workflow.task_input_snapshot",
        payload={
            "draft": {"workflow": {"instructions": "Preserve the admitted request."}}
        },
    )


async def _corrupt_artifact(maker, ref, document):
    # Deliberately corrupt persisted bytes and their transport checksum. This
    # exercises the reader's immutable plan/source identity check, not a fake
    # reader or merely the blob checksum guard.
    payload = json.dumps(document).encode()
    async with maker() as session:
        row = await session.get(TemporalArtifact, ref.removeprefix("artifact:"))
        LocalTemporalArtifactStore(
            Path(settings.workflow.temporal_artifact_root)
        ).write_bytes(row.storage_key, payload, content_type=row.content_type)
        row.sha256 = hashlib.sha256(payload).hexdigest()
        row.size_bytes = len(payload)
        await session.commit()


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
        artifacts = _OnDemandTemporalArtifactService(maker)
        input_ref, input_digest = await _task_input(artifacts)
        monkeypatch.setattr(
            service,
            "resolve_execution_evidence",
            lambda plan, **_: (
                _protected_support_evidence(plan),
                "supported",
            ),
        )
        if reader == "retained":
            # The retained fleet predates certificate-independent admission
            # and rejects the new authority shape via `extra="forbid"`.
            # Only a strict plan keeps the historical wire shape it can
            # parse, so the retained case compiles under explicit strict
            # certification. An ordinary uncertified plan must fail closed
            # on the retained reader (asserted below), never misread as
            # certified.
            monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "protected")
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            session_factory=maker,
            workflow_id=WORKFLOW,
            task_input_snapshot_ref=input_ref,
            task_input_snapshot_digest=input_digest,
            launch_policy_ref="omnigent-on-demand@1",
            plan_store=stores.DbExecutionPlanStore(maker),
        )
        await _admit_inputs(maker, compiled)
        # Artifact bytes and DB payload must both omit the redundant v1 field.
        serialized = await _read_artifact(artifacts, compiled.binding.plan_artifact_ref)
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
                retained_preflight,
                "resolve_deployed_server_build_digest",
                lambda: planned,
            )
            monkeypatch.setattr(
                retained_preflight,
                "_resolve_deployed_host_image_ref",
                lambda _: loaded.payload.hostImageRef,
            )
            retained_preflight.assert_plan_matches_deployed_runtime(loaded.payload)
            monkeypatch.setattr(
                retained_preflight,
                "resolve_deployed_server_build_digest",
                lambda: "sha256:" + "f" * 64,
            )
            with pytest.raises(retained_preflight.OmnigentDeploymentIdentityConflict):
                retained_preflight.assert_plan_matches_deployed_runtime(loaded.payload)
            assert loaded.planRef == compiled.envelope.planRef
            # The strict plan keeps the historical wire shape: no new
            # authority marker for the retained reader to reject.
            assert "admissionMode" not in serialized["payload"]["admissionAuthority"]
            # Skew direction: an ordinary uncertified plan keeps its new
            # authority marker on the wire, so the retained reader must
            # fail closed instead of misreading uncertified admission as
            # certified. Rolling upgrades stay safe in both directions:
            # strict plans admit, uncertified plans reject.
            import copy

            ordinary_wire = copy.deepcopy(serialized)
            authority = compiled.envelope.payload.admissionAuthority
            ordinary_wire["payload"]["admissionAuthority"] = {
                "admissionMode": "ordinary",
                "supportEvidenceRef": "",
                "supportEvidenceDigest": "",
                "supportTier": "uncertified",
                "featureGeneration": authority.featureGeneration,
                "replayCompatibilityVersion": authority.replayCompatibilityVersion,
                "rollbackPolicyVersion": authority.rollbackPolicyVersion,
            }
            with pytest.raises(Exception):
                retained_reader.verify_execution_plan_envelope(ordinary_wire)
        serialized["payload"]["model"]["effort"] = "changed"
        await _corrupt_artifact(maker, compiled.binding.plan_artifact_ref, serialized)
        with pytest.raises(ValueError, match="digest mismatch|supportIdentity"):
            await admission._load_verified_execution_plan(
                compiled.binding, access=ArtifactAccessContext.for_execution(WORKFLOW)
            )
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
        artifacts = _OnDemandTemporalArtifactService(maker)
        input_ref, input_digest = await _task_input(artifacts)
        monkeypatch.setattr(
            service,
            "resolve_execution_evidence",
            lambda plan, **_: (_protected_support_evidence(plan), "supported"),
        )
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            session_factory=maker,
            workflow_id=WORKFLOW,
            task_input_snapshot_ref=input_ref,
            task_input_snapshot_digest=input_digest,
            launch_policy_ref="omnigent-on-demand@1",
            plan_store=stores.DbExecutionPlanStore(maker),
            document_source={
                "upstreamId": "opencode-native-agent",
                "upstreamVersion": "174",
                "upstreamSnapshotDigest": stable_projection,
            },
        )
        await _admit_inputs(maker, compiled)
        planned_source = compiled.envelope.payload.agentSource
        profile_ref = compiled.envelope.payload.agentProfileSnapshotRef.removeprefix(
            "artifact:"
        )
        profile_snapshot = await _read_artifact(artifacts, profile_ref)
        # The incident's precondition: the stable source digest and the
        # profile version digest are different values.
        assert planned_source["upstreamSnapshotDigest"] == stable_projection
        assert profile_snapshot["digest"] != stable_projection

        monkeypatch.setattr(base, "async_session_maker", maker)

        loaded = await admission._load_verified_execution_plan(
            compiled.binding, access=ArtifactAccessContext.for_execution(WORKFLOW)
        )
        assert loaded.payload.agentSource["upstreamSnapshotDigest"] == (
            stable_projection
        )

        # A genuinely different upstream projection must still be rejected.
        conflicting = await _read_artifact(artifacts, profile_ref)
        conflicting["document"]["source"]["upstreamSnapshotDigest"] = (
            "sha256:" + "b" * 64
        )
        await _corrupt_artifact(maker, profile_ref, conflicting)
        with pytest.raises(ValueError, match="conflicts with planned source identity"):
            await admission._load_verified_execution_plan(
                compiled.binding, access=ArtifactAccessContext.for_execution(WORKFLOW)
            )
    finally:
        await engine.dispose()


async def test_plan_compiled_by_the_previous_writer_still_admits(tmp_path, monkeypatch):
    """A durable plan compiled before the stable-source change must still launch.

    Workers upgrade under running schedules and durable executions. The
    previous writer pinned the Agent Profile *version* digest as the upstream
    snapshot digest even when the profile document already carried a distinct
    stable projection digest, so a reader that accepts only the stable identity
    rejects those otherwise valid historical plans.
    """

    stable_projection = "sha256:" + "a" * 64
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/plans.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        artifacts = _OnDemandTemporalArtifactService(maker)
        input_ref, input_digest = await _task_input(artifacts)
        monkeypatch.setattr(
            service,
            "resolve_execution_evidence",
            lambda plan, **_: (_protected_support_evidence(plan), "supported"),
        )
        # Compile through the previous writer: it pinned the profile version
        # digest regardless of the document's own stable source digest.
        monkeypatch.setattr(
            service,
            "stable_upstream_snapshot_digest",
            lambda source, snapshot_digest="": snapshot_digest,
        )
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            session_factory=maker,
            workflow_id=WORKFLOW,
            task_input_snapshot_ref=input_ref,
            task_input_snapshot_digest=input_digest,
            launch_policy_ref="omnigent-on-demand@1",
            plan_store=stores.DbExecutionPlanStore(maker),
            document_source={
                "upstreamId": "opencode-native-agent",
                "upstreamVersion": "174",
                "upstreamSnapshotDigest": stable_projection,
            },
        )
        await _admit_inputs(maker, compiled)
        profile_ref = compiled.envelope.payload.agentProfileSnapshotRef.removeprefix(
            "artifact:"
        )
        profile_snapshot = await _read_artifact(artifacts, profile_ref)
        # The historical shape: the plan pins the version digest while the
        # artifact's document carries a different stable projection digest.
        assert compiled.envelope.payload.agentSource["upstreamSnapshotDigest"] == (
            profile_snapshot["digest"]
        )
        assert profile_snapshot["document"]["source"]["upstreamSnapshotDigest"] == (
            stable_projection
        )
        assert profile_snapshot["digest"] != stable_projection

        monkeypatch.setattr(base, "async_session_maker", maker)

        loaded = await admission._load_verified_execution_plan(
            compiled.binding, access=ArtifactAccessContext.for_execution(WORKFLOW)
        )
        assert loaded.planRef == compiled.envelope.planRef

        # A different Agent Profile artifact must still be rejected, even
        # though the legacy version digest is accepted for historical plans.
        conflicting = await _read_artifact(artifacts, profile_ref)
        conflicting["digest"] = "sha256:" + "c" * 64
        conflicting["document"]["source"]["upstreamSnapshotDigest"] = (
            "sha256:" + "b" * 64
        )
        await _corrupt_artifact(maker, profile_ref, conflicting)
        with pytest.raises(ValueError, match="conflicts with planned source identity"):
            await admission._load_verified_execution_plan(
                compiled.binding, access=ArtifactAccessContext.for_execution(WORKFLOW)
            )
    finally:
        await engine.dispose()
